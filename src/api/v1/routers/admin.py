from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, status, Request, Depends, Path
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.db import get_async_session
from src.db.instrumentManager import instrumentsManager
from src.db.userManager import usersManager
from src.logger import api_logger, database_logger
from src.schemas import InstrumentCreate, InstrumentSchema, BaseAnswer, Deposit
from src.utils.redis_utils import update_cache_after_delete, clear_instruments_cache, clear_user_cache

router = APIRouter(tags=["Admin"], prefix='/admin')


@router.post('/instrument', status_code=status.HTTP_201_CREATED)
async def add_instrument(request: Request,
                         instrument: InstrumentCreate,
                         backgroundTasks: BackgroundTasks,
                         session: AsyncSession = Depends(get_async_session)) -> InstrumentSchema:
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
            backgroundTasks.add_task(usersManager.cancel_order_deleted_user, user.uuid, request_id)

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


@router.delete('/instrument/{ticker}')
async def delete_instrument(request: Request, backgroundTasks: BackgroundTasks,
                            ticker: str = Path(pattern='^[A-Z]{2,10}$'),
                            session: AsyncSession = Depends(get_async_session)) -> BaseAnswer:
    try:
        request_id = request.state.request_id
        deleted_instruments = await instrumentsManager.delete(ticker, session, request_id)
        backgroundTasks.add_task(instrumentsManager.cancel_order_deleted_ticker, deleted_instruments.id, request_id)
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
        await usersManager.deposit_user(session, deposit_obj, request.state.request_id)
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
                   request: Request,
                   session: AsyncSession = Depends(get_async_session)) -> BaseAnswer:
    try:
        await usersManager.withdraw_user(session, deposit_obj, request.state.request_id)

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
