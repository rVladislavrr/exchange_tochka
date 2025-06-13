from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, status, Request, Depends, Path
from pydantic import UUID4, BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import selectinload

from src import schemas
from src.db.db import get_async_session, async_session_maker
from src.db.instrumentManager import instrumentsManager
from src.db.users import usersManager
from src.logger import api_logger, database_logger, cache_logger
from src.models import Instruments, Users, UserBalances, Orders
from src.models.orders import StatusEnum, SideEnum
from src.redis_conn import redis_client
from src.schemas import InstrumentCreate
from src.schemas.deposit import Deposit
from src.utils.redis_utils import update_cache_after_delete, clear_instruments_cache, clear_user_cache

router = APIRouter(tags=["Admin"], prefix='/admin')


class BaseAnswer(BaseModel):
    success: bool = True


@router.post('/instrument', status_code=status.HTTP_201_CREATED, )
async def add_instrument(request: Request,
                         instrument: InstrumentCreate,
                         backgroundTasks: BackgroundTasks,
                         session: AsyncSession = Depends(get_async_session)):
    try:
        request_id = request.state.request_id
        instrumentORM = await instrumentsManager.create(session, dict(instrument), request_id)
        backgroundTasks.add_task(clear_instruments_cache, request.state.request_id)
        api_logger.info(
            f"[{request.state.request_id}] Create Instrument",
            extra={
                "status_code": 201,
                "id": instrumentORM.id,
            }
        )
        await session.close()
        return instrumentORM
    except HTTPException as e:
        api_logger.warning(
            f"[{request.state.request_id}] Canceled The Creation Instrument",
            extra={
                "status_code": e.status_code,
                "ticker": instrument.ticker,
                "detail": e.detail,
            }
        )
        raise
    except Exception as e:
        api_logger.error(
            f"[{request.state.request_id}] add_instrument unknown error",
            exc_info=e
        )
        raise HTTPException(500)
    finally:
        await session.close()


async def cancel_order_deleted_user(user_id, request_id):
    try:
        async with async_session_maker() as session:
            res = await session.execute(select(Orders).options(selectinload(Orders.instrument)).where(
                Orders.user_uuid == user_id,
                or_(
                    Orders.status == StatusEnum.NEW,
                    Orders.status == StatusEnum.PARTIALLY_EXECUTED
                )
            )
            )
            orders = res.scalars()
            r = await redis_client.get_redis()
            pipe = r.pipeline()

            for order in orders:
                key = f"{int(order.price)}:{int(order.qty - order.filled)}:{order.uuid}:{round(order.create_at.timestamp(), 3)}"
                orderbook_key = f"orderbook:{order.ticker}:{'asks' if order.side == SideEnum.SELL else 'bids'}"
                pipe.zrem(orderbook_key, key)
                pipe.hdel('active_orders', str(order.uuid))
                old_status = order.status
                order.status = StatusEnum.CANCELLED
                database_logger.info(
                    f"[{request_id}] Cancel Order (user)",
                    extra={"id ": str(order.uuid), "old status": old_status.value, 'user_id': str(user_id),
                           "side": order.side.value,
                           "ticker": order.ticker,
                           "price": order.price}
                )
                cache_logger.info(
                    f"[{request_id}] Cancel Order  (user) cache ",
                    extra={"orderbook_key": orderbook_key, "key": key}
                )

            await pipe.execute()
            await session.commit()
            await session.close()
    except Exception as e:
        database_logger.error(
            f"[{request_id}] Cancel Order (DELETE USER)",
            exc_info=e
        )
        cache_logger.info(
            f"[{request_id}] Cancel Order CACHE (DELETE USER)",
            exc_info=e)
        raise HTTPException(500)


@router.delete('/user/{user_id}')
async def delete_user(request: Request, user_id: UUID4,
                      backgroundTasks: BackgroundTasks,
                      session: AsyncSession = Depends(get_async_session)) -> schemas.UserRegister:
    try:
        request_id = request.state.request_id

        if user := await usersManager.get_user_uuid(user_id, session):

            if user.role.value == "ADMIN":
                raise HTTPException(status_code=403, detail="FORBIDDEN, you cant disable admin")

            if not user.is_active:
                raise HTTPException(status_code=400, detail="User already deleted")

            user.is_active = False
            user.delete_at = datetime.now()
            await session.commit()
            database_logger.info(
                f"[{request.state.request_id}] Delete user",
                extra={
                    "user_id": str(user_id),
                }
            )

            backgroundTasks.add_task(clear_user_cache, user.api_key, request_id)
            backgroundTasks.add_task(cancel_order_deleted_user, user.uuid, request_id)

            api_logger.info(
                f"[{request.state.request_id}] Delete user",
                extra={
                    "user_id": str(user_id),
                }
            )
            await session.close()
            return user
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    except HTTPException as e:
        api_logger.warning(
            f"[{request.state.request_id}] Cannot Delete User",
            extra={
                'user_id': str(user_id),
                "status_code": e.status_code,
                "detail": e.detail
            }
        )
        raise
    except Exception as e:
        api_logger.error(
            f"[{request.state.request_id}] delete_user unknown error",
            exc_info=e
        )
        raise HTTPException(500)
    finally:
        await session.close()


async def cancel_order_deleted_ticker(id_instrument, request_id):
    try:
        async with async_session_maker() as session:
            res = await session.execute(
                select(Instruments).options(selectinload(Instruments.orders))
                .where(
                    Instruments.id == id_instrument,
                )
            )
            instruments = res.scalar_one_or_none()
            r = await redis_client.get_redis()
            pipe = r.pipeline()

            for order in instruments.orders:
                if order.status == StatusEnum.EXECUTED or order.status == StatusEnum.CANCELLED:
                    continue
                key = f"{int(order.price)}:{int(order.qty - order.filled)}:{order.uuid}:{round(order.create_at.timestamp(), 3)}"
                orderbook_key = f"orderbook:{order.ticker}:{'asks' if order.side == SideEnum.SELL else 'bids'}"
                order.status = StatusEnum.CANCELLED
                pipe.zrem(orderbook_key, key)
                pipe.hdel('active_orders', str(order.uuid))

                cache_logger.info(
                    f"[{request_id}] cancel order (instrument)",
                    extra={"orderbook_key": orderbook_key, 'key': key}
                )

                if order.side == SideEnum.BUY:
                    userBalanceRUB = await usersManager.get_user_balance_by_ticker(
                        session, order.user_uuid, ticker='RUB', create_if_missing=True
                    )
                    userBalanceRUB.frozen_balance -= order.price * (order.qty - order.filled)
                    userBalanceRUB.available_balance += order.price * (order.qty - order.filled)
                    database_logger.info(
                        "Update balance(Cancel Order instrument)",
                        extra={
                            "user_id": str(order.user_uuid),
                            "ticker": order.ticker,
                            "available_balance +=": order.price * (order.qty - order.filled),
                            "frozen_balance -=": order.price * (order.qty - order.filled)
                        }
                    )
                database_logger.info(
                    f"[{request_id}] Cancel Order ( instrument )",
                    extra={"id ": str(order.uuid), 'user_id': str(order.user_uuid),
                           "side": order.side.value,
                           "ticker": order.ticker,
                           "price": order.price}
                )
            await pipe.execute()
            await session.commit()
            await session.close()
    except Exception as e:
        database_logger.error(
            f"[{request_id}] Cancel Order (DELETE instrument)",
            exc_info=e
        )
        cache_logger.info(
            f"[{request_id}] Cancel Order CACHE (DELETE instrument)",
            exc_info=e)
    finally:
        await session.close()


@router.delete('/instrument/{ticker}')
async def delete_instrument(request: Request, backgroundTasks: BackgroundTasks,
                            ticker: str = Path(pattern='^[A-Z]{2,10}$'),
                            session: AsyncSession = Depends(get_async_session)) -> BaseAnswer:
    try:
        request_id = request.state.request_id
        deleted_instruments = await instrumentsManager.delete(ticker, session, request_id)
        backgroundTasks.add_task(cancel_order_deleted_ticker, deleted_instruments.id, request_id)
        backgroundTasks.add_task(update_cache_after_delete, ticker, request_id)
        api_logger.info(
            f"[{request.state.request_id}] Delete instrument",
            extra={
                "ticker": ticker,
                'id': deleted_instruments.id
            }
        )
        await session.close()
        return BaseAnswer()
    except HTTPException as e:
        api_logger.warning(
            f"[{request.state.request_id}] Cannot delete instrument",
            extra={
                "ticker": ticker,
                "status_code": e.status_code,
                "detail": e.detail
            }
        )
        raise
    except Exception as e:
        api_logger.error(
            f"[{request.state.request_id}] delete_instrument unknown error",
            exc_info=e
        )
        raise HTTPException(500)
    finally:
        await session.close()


@router.post('/balance/deposit')
async def deposit(request: Request, deposit_obj: Deposit,
                  session: AsyncSession = Depends(get_async_session)) -> BaseAnswer:
    try:
        stmt = (
            select(Users, Instruments, UserBalances)
            .select_from(Users)
            .join(
                Instruments,
                and_(
                    Instruments.ticker == deposit_obj.ticker,
                    Instruments.is_active == True),
                isouter=True,
            )
            .join(
                UserBalances,
                and_(
                    UserBalances.user_uuid == Users.uuid,
                    UserBalances.instrument_id == Instruments.id,
                ),
                isouter=True,
            )
            .where(Users.uuid == deposit_obj.user_id)
            .limit(1)
        )

        result = await session.execute(stmt)
        user, instrument, user_balance = result.first() or (None, None, None)

        if not user:
            raise HTTPException(404, "User not found")

        if deposit_obj.ticker == 'RUB':
            try:
                userBalanceRUB = await usersManager.get_user_balance_by_ticker(
                    session, deposit_obj.user_id, ticker='RUB', create_if_missing=True
                )
                userBalanceRUB.available_balance += deposit_obj.amount
                await session.commit()
                database_logger.info(
                    f"[{request.state.request_id}] Deposit",
                    extra={
                        'user': str(deposit_obj.user_id),
                        'ticker': deposit_obj.ticker,
                        'amount': deposit_obj.amount,
                    }
                )

            except Exception as e:
                database_logger.error(
                    f"[{request.state.request_id}] Cannot deposit",
                    exc_info=e
                )
            api_logger.info(
                f"[{request.state.request_id}] Deposit",
                extra={
                    'user': str(deposit_obj.user_id),
                    'ticker': deposit_obj.ticker,
                    'amount': deposit_obj.amount,
                }
            )
            await session.close()

            return BaseAnswer()

        if not instrument:
            await session.close()
            raise HTTPException(404, "Instrument not found")

        try:
            if user_balance:
                user_balance.available_balance += deposit_obj.amount
            else:
                user_balance = UserBalances(
                    user_uuid=user.uuid,
                    instrument_id=instrument.id,
                    available_balance=deposit_obj.amount,
                    frozen_balance=0,
                )
                session.add(user_balance)
            await session.commit()
            database_logger.info(
                f"[{request.state.request_id}] Deposit",
                extra={
                    'user': str(deposit_obj.user_id),
                    'ticker': deposit_obj.ticker,
                    'amount': deposit_obj.amount,
                }
            )
        except SQLAlchemyError as e:
            await session.rollback()
            raise e
        finally:
            await session.close()

        api_logger.info(
            f"[{request.state.request_id}] Deposit",
            extra={
                'user': str(deposit_obj.user_id),
                'ticker': deposit_obj.ticker,
                'amount': deposit_obj.amount,
            }
        )

        return BaseAnswer()
    except HTTPException as e:
        api_logger.warning(
            f"[{request.state.request_id}] Cannot deposit",
            extra={
                'status_code': e.status_code,
                'detail': e.detail
            }
        )
        raise
    except Exception as e:
        api_logger.error(
            f"[{request.state.request_id}] deposit unknown error",
            exc_info=e
        )
        raise HTTPException(500)
    finally:
        await session.close()


@router.post('/balance/withdraw')
async def withdraw(deposit_obj: Deposit,
                    request:Request,
                   session: AsyncSession = Depends(get_async_session)) -> BaseAnswer:
    try:
        stmt = (
            select(UserBalances)
            .join(
                Instruments,
                Instruments.ticker == deposit_obj.ticker,
            )
            .where(UserBalances.user_uuid == deposit_obj.user_id, UserBalances.instrument_id == Instruments.id)
            .limit(1)
        )
        result = await session.execute(stmt)
        (userBalances,) = result.first() or (None,)
        if not userBalances:
            raise HTTPException(status_code=400, detail="Not enough balance or Not user or Not ticker")

        if userBalances.available_balance < deposit_obj.amount:
            raise HTTPException(status_code=400, detail="Not enough balance")
        try:
            userBalances.available_balance -= deposit_obj.amount
            database_logger.info(
                f"[{request.state.request_id}] Withdraw",
                extra={
                    'user': str(deposit_obj.user_id),
                    'ticker': deposit_obj.ticker,
                    'amount': deposit_obj.amount,
                }
            )
            await session.commit()
        except SQLAlchemyError as e:
            await session.rollback()
            raise e
        finally:
            await session.close()

        api_logger.info(
            f"[{request.state.request_id}] Withdraw",
            extra={
                'user': str(deposit_obj.user_id),
                'ticker': deposit_obj.ticker,
                'amount': deposit_obj.amount,
            }
        )

        return BaseAnswer()
    except HTTPException as e:

        api_logger.warning(
            f"[{request.state.request_id}] Cannot withdraw",
            extra={"status_code": e.status_code, "detail": e.detail}
        )
        raise

    except Exception as e:

        api_logger.error(
            f"[{request.state.request_id}] withdraw unknown error",
            exc_info=e
        )

        raise HTTPException(500)
    finally:
        await session.close()
