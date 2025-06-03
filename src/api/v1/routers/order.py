from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks, status
from pydantic import UUID4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.db import get_async_session
from src.db.orderManager import orderManager
from src.db.users import usersManager
from src.logger import api_logger, cache_logger, database_logger
from src.models import Orders, Users
from src.models.orders import SideEnum, StatusEnum
from src.redis_conn import redis_client
from src.schemas.order import MarketOrder, LimitOrder, create_GetOrder
from src.tasks.orders import match_order_limit, execution_orders
from src.utils.redis_utils import check_ticker_exists, calculate_order_cost
from src.tasks.celery_tasks import match_order_limit2

router = APIRouter(prefix="/order", tags=["orders"])


@router.get('/{order_id}')
async def get_order(request: Request,
                    order_id: UUID4,
                    session: AsyncSession = Depends(get_async_session)):
    request_id = request.state.request_id
    try:
        orderOrm = (await session.execute(
            select(Orders).options(selectinload(Orders.instrument)).where(Orders.uuid == order_id,
                                                                          Orders.user_uuid == request.state.user.id)
        )).scalars().one_or_none()
        if not orderOrm:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Order not found")

        order = create_GetOrder(orderOrm)
        api_logger.info(
            f"[{request_id}] Get order",
            extra={'order_id': str(order_id)}
        )
        return order.model_dump(exclude_none=True)
    except HTTPException as e:
        api_logger.warning(
            f"[{request_id}] Get order",
            extra={'order_id': str(order_id), 'detail': e.detail, }
        )
        raise
    except Exception as e:
        api_logger.error(
            f"[{request_id}] BAD Get order",
            extra={'order_id': str(order_id), }
        )
        raise HTTPException(500)


@router.delete('/{order_id}')
async def cancel_order(request: Request,
                       order_id: UUID4, session: AsyncSession = Depends(get_async_session)):
    request_id = request.state.request_id
    try:
        r = await redis_client.get_redis()
        order = await r.hget('active_orders', str(order_id))

        if not order:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        try:
            query = select(Orders).options(selectinload(Orders.instrument)).where(Orders.uuid == order_id,)
            orderOrm = (await session.execute(query)).scalar_one()
            key = f"{int(orderOrm.price)}:{int(orderOrm.qty - orderOrm.filled)}:{orderOrm.uuid}"
            orderbook_key = f"orderbook:{orderOrm.ticker}:{'asks' if orderOrm.side == SideEnum.SELL else 'bids'}"

            pipe = r.pipeline()
            pipe.zrem(orderbook_key, key)
            await pipe.execute()
            cache_logger.info(
                f"[{request_id}] cancel_order cache (delete cache)",
                extra={'order_id': str(order_id)}
            )
        except Exception as e:
            cache_logger.error(
                f"[{request_id}] cancel_order (delete cache)",
                extra={'order_id': str(order_id)},
                exc_info=e
            )
            raise

        if orderOrm.side == SideEnum.BUY:
            userBalanceRUB = await usersManager.get_user_balance_by_ticker(
                session, orderOrm.user_uuid, ticker='RUB', create_if_missing=True
            )
            summa = orderOrm.price * (orderOrm.qty - orderOrm.filled)
            userBalanceRUB.frozen_balance -= summa
            userBalanceRUB.available_balance += summa
        else:
            userBalanceTicker = await usersManager.get_user_balance_by_ticker(
                session, orderOrm.user_uuid, ticker=orderOrm.ticker, create_if_missing=True
            )
            userBalanceTicker.frozen_balance -= orderOrm.qty
            userBalanceTicker.available_balance += orderOrm.qty

        orderOrm.status = StatusEnum.CANCELLED
        database_logger.info(
            f"[{request_id}] cancel_order",
            extra={'order_id': str(order_id)}
        )
        await session.commit()
    except HTTPException as e:
        api_logger.warning(
            f"[{request_id}] cancel_order",
            extra={'order_id': str(order_id), 'detail': e.detail, }
        )
        raise
    except Exception as e:
        database_logger.error(
            f"[{request_id}] cancel_order",
            extra={'order_id': str(order_id)}
        )
        api_logger.error(
            f"[{request_id}] cancel_order",
            extra={'order_id': str(order_id)}
        )
        raise HTTPException(500)

    api_logger.info(
        f"[{request_id}] cancel_order",
        extra={'order_id': str(order_id)}
    )

    return {"success": True}


@router.get('')
async def get_list_orders(request: Request,
                          session: AsyncSession = Depends(get_async_session)):
    user = request.state.user
    request_id = request.state.request_id
    try:
        user = (await session.execute(
            select(Users).options(selectinload(Users.orders).selectinload(Orders.instrument)).where(
                Users.uuid == user.id)
        )).scalars().one()
        api_logger.info(
            f"[{request_id}] get_order list",
            extra={'user_id': str(request.state.user.id)}
        )
        return [create_GetOrder(order).model_dump(exclude_none=True) for order in user.orders]
    except Exception as e:
        api_logger.error(
            f"[{request_id}] get_order list",
            exc_info=e
        )
        raise HTTPException(500)

async def create_cancel_order(user, session, instrument_id, order_data, request_id):
    try:
        orderOrm = await orderManager.create_orderOrm(user, session, instrument_id, order_data)
        orderOrm.status = StatusEnum.CANCELLED
        await session.flush()
        database_logger.info(
            f"[{request_id}] Create MarketOrder CANCELLED",
            extra={'user_id': str(user.id), 'order_id': str(orderOrm.uuid)}
        )
        return orderOrm
    except Exception as e:
        database_logger.error(
            f"[{request_id}] Create MarketOrder CANCELLED",
            extra={'user_id': str(user.id), 'instrument_id':str(instrument_id)}
        )



# TODO: фоновые задачи будут в celery, но пока в background_tasks
# TODO: раскидать на множество функций но пока чёт так лень
@router.post("")
async def create_order(request: Request, background_tasks: BackgroundTasks,
                       order_data: LimitOrder | MarketOrder,
                       session: AsyncSession = Depends(get_async_session)):
    r = await redis_client.get_redis()
    user = request.state.user
    request_id = request.state.request_id
    try:
        instrument_id = await check_ticker_exists(order_data.ticker, session)
        userBalanceRub = await usersManager.get_user_balance_by_ticker(
            session, user.id, ticker="RUB", create_if_missing=True
        )
        userBalanceTicker = await usersManager.get_user_balance_by_ticker(
            session, user.id, ticker=order_data.ticker, create_if_missing=True
        )

        if order_data.direction == SideEnum.SELL:

            # либо вообще их нет либо не хватает
            if (not userBalanceTicker or
                    order_data.qty > userBalanceTicker.available_balance):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Not enough balance order_data.qty {order_data.qty} > your balance'"
                                           f"{userBalanceTicker.available_balance or None}'")

            if isinstance(order_data, MarketOrder):
                try:
                    # самая дорогая продажа и вообще существует ли она
                    total_cost, matched_orders = await calculate_order_cost(r, order_data.ticker,
                                                                            order_data.qty, order_data.direction.value)
                except ValueError as e:
                    orderOrm = await create_cancel_order(user, session, instrument_id, order_data, request_id)
                    await session.commit()
                    await session.close()

                    api_logger.info(
                        f"[{request_id}] create order CANCELLED",
                        extra={'user_id': str(user.id), 'order_id': str(orderOrm.uuid)}
                    )
                    return {"order_id": orderOrm.uuid,
                            "success": True}

        else:  # order_data.direction == SideEnum.BUY
            if isinstance(order_data, MarketOrder):
                # при рыночном пытаемся собрать самую дешёвую покупку и проверяем от этого его баланс
                try:
                    total_cost, matched_orders = await calculate_order_cost(r, order_data.ticker,
                                                                            order_data.qty, order_data.direction.value)
                except ValueError as e:
                    orderOrm = await create_cancel_order(user, session, instrument_id, order_data, request_id)
                    await session.commit()
                    await session.close()
                    api_logger.info(
                        f"[{request_id}] create order CANCELLED",
                        extra={'user_id': str(user.id), 'order_id': str(orderOrm.uuid)}
                    )
                    return {"order_id": orderOrm.uuid,
                            "success": True}
                if total_cost > userBalanceRub.available_balance:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                        detail='Not enough balance total_cost = '
                                               '{}, your balance = {}'
                                        .format(total_cost, userBalanceRub.available_balance))
            else:  # isinstance(order_data, LimitOrder)
                # при лимитном просто перемножаем и проверяем есть ли у пользователя такое колво денег
                if order_data.qty * order_data.price > userBalanceRub.available_balance:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                        detail='Not enough balance total_cost = {}, your balance = {}'.format(
                                            order_data.qty * order_data.price,
                                            userBalanceRub.available_balance))
    except HTTPException as e:
        await session.close()
        api_logger.warning(
            f"[{request_id}] create order | balance",
            extra={'user_id': str(user.id), 'status_code': e.status_code, 'detail': e.detail, }
        )
        raise
    except Exception as e:
        await session.close()
        api_logger.error(
            f"[{request_id}] create order failed",
            extra={'user_id': str(user.id), },
            exc_info=e
        )
        raise HTTPException(500)
    try:
        orderOrm = await orderManager.create_orderOrm(user, session, instrument_id, order_data)
        if isinstance(order_data, MarketOrder):
            await execution_orders(orderOrm, order_data.ticker,
                                   userBalanceRub, userBalanceTicker,
                                   matched_orders, total_cost, session, r)

        else:
            background_tasks.add_task(match_order_limit, orderOrm, order_data.ticker, request_id)
            await session.commit()
        return {"order_id": orderOrm.uuid,
                "success": True}
    except HTTPException as e:
        api_logger.warning(
            f"[{request_id}] market order failed",
            extra={'detail': e.detail, 'status_code': e.status_code}
        )
        raise
    except Exception as e:
        api_logger.error(
            f"[{request_id}] market order failed",
            exc_info=e,
        )
        raise HTTPException(500)
    finally:
        await session.close()
