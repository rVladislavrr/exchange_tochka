from datetime import timezone

from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks, status
from pydantic import UUID4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.db import get_async_session
from src.db.orderManager import orderManager
from src.db.users import usersManager
from src.logger import api_logger, cache_logger, database_logger
from src.models import Orders, TradeLog, Users
from src.models.orders import SideEnum, StatusEnum
from src.redis_conn import redis_client
from src.schemas.order import MarketOrder, LimitOrder, create_GetOrder
from src.tasks.orders import match_order_limit, add_tradeLog_redis
from src.utils.redis_utils import check_ticker_exists, calculate_order_cost, update_match_orders
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
        orderOrm = (await session.execute(
            select(Orders).options(selectinload(Orders.instrument)).where(Orders.uuid == order_id, )
        )).scalar_one_or_none()

        if not orderOrm:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        if str(orderOrm.user_uuid) != str(request.state.user.id) and request.state.user.role != "ADMIN":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

        if orderOrm.status in {StatusEnum.EXECUTED, StatusEnum.CANCELLED}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

        try:
            key = f"{int(orderOrm.price)}:{int(orderOrm.qty - orderOrm.filled)}:{orderOrm.uuid}"
            orderbook_key = f"orderbook:{orderOrm.ticker}:{'asks' if orderOrm.side == SideEnum.SELL else 'bids'}"

            r = await redis_client.get_redis()
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
                    orderOrm = await orderManager.create_orderOrm(user, session, instrument_id, order_data)
                    orderOrm.status = StatusEnum.CANCELLED
                    await session.commit()
                    database_logger.info(
                        f"[{request_id}] Create MarketOrder CANCELLED",
                        extra={'user_id': str(user.id),'order_id': str(orderOrm.uuid)}
                    )
                    api_logger.info(
                        f"[{request_id}] create order CANCELLED",
                        extra={'user_id': str(user.id), 'order_id': str(orderOrm.uuid)}
                    )
                    return {"order_id": orderOrm.uuid,
                            "success": True}

        else:
            if isinstance(order_data, MarketOrder):
                # при рыночном пытаемся собрать самую дешёвую покупку и проверяем от этого его баланс
                try:
                    total_cost, matched_orders = await calculate_order_cost(r, order_data.ticker,
                                                                            order_data.qty, order_data.direction.value)
                except ValueError as e:
                    orderOrm = await orderManager.create_orderOrm(user, session, instrument_id, order_data)
                    orderOrm.status = StatusEnum.CANCELLED
                    await session.commit()
                    database_logger.info(
                        f"[{request_id}] Create MarketOrder CANCELLED",
                        extra={'user_id': str(user.id), 'order_id': str(orderOrm.uuid)}
                    )
                    api_logger.info(
                        f"[{request_id}] create order CANCELLED",
                        extra={'user_id': str(user.id), 'order_id': str(orderOrm.uuid)}
                    )
                    return {"order_id": orderOrm.uuid,
                            "success": True}
                if total_cost > userBalanceRub.available_balance:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Not enough balance total_cost = '
                                                                                        '{}, your balance = {}'
                                        .format(order_data.qty * order_data.price, userBalanceRub.available_balance))
            else:
                # при лимитном просто перемножаем и проверяем есть ли у пользователя такое колво денег
                if order_data.qty * order_data.price > userBalanceRub.available_balance:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                        detail='Not enough balance total_cost = {}, your balance = {}'.format(
                                            order_data.qty * order_data.price,
                                            userBalanceRub.available_balance))
    except HTTPException as e:
        api_logger.warning(
            f"[{request_id}] create order",
            extra={'user_id': str(user.id), 'status_code': e.status_code, 'detail': e.detail,}
        )
        raise
    except Exception as e:
        api_logger.error(
            f"[{request_id}] create order failed",
            extra={'user_id': str(user.id),},
            exc_info=e
        )
        raise HTTPException(500)
    try:
        orderOrm = await orderManager.create_orderOrm(user, session, instrument_id, order_data)
        if isinstance(order_data, MarketOrder):
            # быстрое обновление тк точно знаем что выполниться
            pipe = r.pipeline()
            update_match_orders(pipe, matched_orders, order_data.ticker, order_data.direction)
            await pipe.execute()

            # рыночная продажа
            if order_data.direction == SideEnum.SELL:
                userBalanceRub.available_balance += total_cost
                userBalanceTicker.available_balance -= order_data.qty

                pipe = r.pipeline()

                for item in matched_orders:

                    buy_order_uuid = item.get("uuid")
                    price = item.get("price")
                    quantity = item.get("quantity")
                    total_cost = item.get("cost")

                    order_result = await session.execute(
                        select(Orders).where(Orders.uuid == buy_order_uuid)
                    )
                    buy_order = order_result.scalar_one()

                    buy_balance = await usersManager.get_user_balance_by_ticker(
                        session, buy_order.user_uuid, ticker=order_data.ticker, create_if_missing=True
                    )

                    rub_balance = await usersManager.get_user_balance_by_ticker(
                        session, buy_order.user_uuid, ticker="RUB", create_if_missing=True
                    )

                    rub_balance.frozen_balance -= total_cost
                    buy_balance.available_balance += quantity

                    # 3. Добавить в TradeLog
                    trade = TradeLog(
                        sell_order_id=orderOrm.uuid,
                        buy_order_id=buy_order.uuid,
                        price=price,
                        quantity=quantity,
                        ticker=order_data.ticker
                    )
                    session.add(trade)
                    add_tradeLog_redis(pipe, order_data.ticker, {
                        "ticker": order_data.ticker,
                        "amount": quantity,
                        "price": price,
                        "timestamp": trade.create_at.replace(tzinfo=timezone.utc).isoformat(),
                    })

                    # 4. Обновить статус ордера, если исполнен
                    buy_order.filled = (buy_order.filled or 0) + quantity
                    if buy_order.filled >= buy_order.qty:
                        buy_order.status = StatusEnum.EXECUTED
                    else:
                        buy_order.status = StatusEnum.PARTIALLY_EXECUTED

                await session.commit()
                await pipe.execute()
            else:  # order_data.direction == Direction.BUY

                userBalanceRub.available_balance -= total_cost
                userBalanceTicker.available_balance += orderOrm.qty


                pipe = r.pipeline()

                for item in matched_orders:

                    sell_order_uuid = item.get("uuid")
                    price = item.get("price")
                    quantity = item.get("quantity")
                    total_cost = item.get("cost")

                    order_result = await session.execute(
                        select(Orders).where(Orders.uuid == sell_order_uuid)
                    )

                    sell_order = order_result.scalar_one()

                    sell_balance = await usersManager.get_user_balance_by_ticker(
                        session, sell_order.user_uuid, ticker=order_data.ticker, create_if_missing=True
                    )

                    rub_balance = await usersManager.get_user_balance_by_ticker(
                        session, sell_order.user_uuid, ticker="RUB", create_if_missing=True
                    )

                    rub_balance.available_balance += total_cost
                    sell_balance.frozen_balance -= quantity

                    # 3. Добавить в TradeLog
                    trade = TradeLog(
                        sell_order_id=sell_order.uuid,
                        buy_order_id=orderOrm.uuid,
                        price=price,
                        quantity=quantity,
                        ticker=order_data.ticker
                    )
                    session.add(trade)
                    add_tradeLog_redis(pipe, order_data.ticker, {
                        "ticker": order_data.ticker,
                        "amount": quantity,
                        "price": price,
                        "timestamp": trade.create_at.replace(tzinfo=timezone.utc).isoformat(),
                    })

                    # 4. Обновить статус ордера, если исполнен
                    sell_order.filled = (sell_order.filled or 0) + quantity
                    if sell_order.filled >= sell_order.qty:
                        sell_order.status = StatusEnum.EXECUTED
                    else:
                        sell_order.status = StatusEnum.PARTIALLY_EXECUTED

                await session.commit()
                await pipe.execute()

        else:
            background_tasks.add_task(match_order_limit, orderOrm, order_data.ticker)
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

