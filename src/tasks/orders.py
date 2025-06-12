import json
from datetime import timezone
from src.db.db import async_session_maker
from src.db.users import usersManager
from src.logger import database_logger
from src.models import Orders, TradeLog
from src.models.orders import StatusEnum, TypeEnum, SideEnum
from src.redis_conn import redis_client
from src.utils.redis_utils import match_limit_order, update_match_orders


async def execution_orders(orderOrm: Orders, ticker, userRub,
                           userTicker, matched_orders,
                           total_cost, session, redis_c, remaining_qty_order=None):
    # быстрый update смаченных ордеров
    pipe = redis_c.pipeline()
    update_match_orders(pipe, matched_orders, ticker, orderOrm.side)
    await pipe.execute()

    if orderOrm.order_type == TypeEnum.MARKET_ORDER:
        if orderOrm.side == SideEnum.SELL:
            userRub.available_balance += total_cost
            userTicker.available_balance -= orderOrm.qty
        else:
            userRub.available_balance -= total_cost
            userTicker.available_balance += orderOrm.qty
    else:
        if orderOrm.side == SideEnum.SELL:
            userRub.available_balance += total_cost
            userTicker.available_balance -= (orderOrm.qty - remaining_qty_order)

        else:
            userRub.available_balance -= total_cost
            userTicker.available_balance += (orderOrm.qty - remaining_qty_order)
    await session.commit()

    pipe = redis_c.pipeline()

    for item in matched_orders:
        match_order_uuid = item.get("uuid")
        price = item.get("price")
        quantity = item.get("quantity")
        total_cost = item.get("cost")

        match_order = await session.get(Orders, match_order_uuid)

        match_user_rub_balance = await usersManager.get_user_balance_by_ticker(
            session, match_order.user_uuid, ticker="RUB", create_if_missing=True
        )

        match_user_ticker_balance = await usersManager.get_user_balance_by_ticker(
            session, match_order.user_uuid, ticker=ticker, create_if_missing=True
        )

        if orderOrm.side == SideEnum.SELL:
            match_user_rub_balance.frozen_balance -= total_cost
            match_user_ticker_balance.available_balance += quantity

        else:
            match_user_rub_balance.available_balance += total_cost
            match_user_ticker_balance.frozen_balance -= quantity

        match_order.filled = (match_order.filled or 0) + quantity
        if match_order.filled >= match_order.qty:
            match_order.status = StatusEnum.EXECUTED
        else:
            match_order.status = StatusEnum.PARTIALLY_EXECUTED

        await session.commit()

        trade = TradeLog(
            sell_order_id=orderOrm.uuid if orderOrm.side == SideEnum.SELL else match_order_uuid,
            buy_order_id=match_order_uuid if orderOrm.side == SideEnum.SELL else orderOrm.uuid,
            price=price,
            quantity=quantity,
            ticker=ticker
        )
        session.add(trade)
        await session.commit()

        add_tradeLog_redis(pipe, ticker, {
            "ticker": ticker,
            "amount": quantity,
            "price": price,
            "timestamp": trade.create_at.replace(tzinfo=timezone.utc).isoformat(),
        })
    await pipe.execute()


def add_tradeLog_redis(pipe, ticker: str, data: dict):
    key = f"ticker:{ticker}"
    pipe.lpush(key, json.dumps(data))
    pipe.ltrim(key, 0, 199)


async def match_order_limit(orderOrm_uuid, ticker: str, request_id, r=None):
    try:
        if not r:
            r = await redis_client.get_redis()
        print('1')
        async with async_session_maker() as session:
            orderOrm = await session.get(Orders, orderOrm_uuid)
            try:
                userBalanceRUB = await usersManager.get_user_balance_by_ticker(
                    session, orderOrm.user_uuid, 'RUB', create_if_missing=True
                )

                userBalanceTicker = await usersManager.get_user_balance_by_ticker(
                    session, orderOrm.user_uuid, ticker=ticker, create_if_missing=True
                )
                total_cost, matched_orders, remaining_qty_order = await match_limit_order(r, ticker,
                                                                                          orderOrm.qty, orderOrm.price,
                                                                                          orderOrm.side.value)
                print('2')
                if matched_orders:

                    orderOrm.status = StatusEnum.EXECUTED if remaining_qty_order == 0 else StatusEnum.PARTIALLY_EXECUTED
                    if orderOrm.status == StatusEnum.EXECUTED:
                        orderOrm.filled = orderOrm.qty
                    elif orderOrm.status == StatusEnum.PARTIALLY_EXECUTED:
                        orderOrm.filled = orderOrm.qty - remaining_qty_order
                    database_logger.info(
                        f"[{request_id}] Background Task 1 status",
                        extra={"order_uuid": str(orderOrm.uuid), 'status': orderOrm.status.value},
                    )
                    await execution_orders(
                        orderOrm, ticker, userBalanceRUB, userBalanceTicker, matched_orders, total_cost, session, r,
                        remaining_qty_order
                    )
                    await session.commit()
                    print(3)
            except Exception as e:
                database_logger.error(
                    f"[{request_id}] Background Task 1 error: {e}",
                    exc_info=True,
                    extra={"order_uuid": str(orderOrm.uuid)},
                )
            try:
                if orderOrm.side == SideEnum.BUY:
                    # Списали уже реально потраченное в userBalanceRUB.available_balance -= total_cost выше
                    # Теперь заморозить только остаток заявки на будущие сделки
                    remaining_reserved = remaining_qty_order * orderOrm.price
                    userBalanceRUB.frozen_balance += remaining_reserved
                    userBalanceRUB.available_balance -= remaining_reserved
                else:  # orderOrm.side == SideEnum.SELL:
                    # Продажа: заморозить неисполненный объём
                    userBalanceTicker.available_balance -= remaining_qty_order
                    userBalanceTicker.frozen_balance += remaining_qty_order
                print(4)
                if remaining_qty_order > 0:
                    orderbook_key_add = f"orderbook:{ticker}:{'asks' if orderOrm.side == SideEnum.SELL else 'bids'}"
                    new_entry_add = f"{int(orderOrm.price)}:{int(remaining_qty_order)}:{orderOrm.uuid}"
                    await r.zadd(orderbook_key_add, {new_entry_add: orderOrm.price})
                    await r.hset('active_orders', str(orderOrm.uuid), "active")

                await session.commit()
            except Exception as e:
                database_logger.error(
                    "Background task failed 2",
                    exc_info=e,
                )

    except Exception as e:
        await session.rollback()
        print(e)
