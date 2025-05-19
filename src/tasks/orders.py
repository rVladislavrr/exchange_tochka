from sqlalchemy import select

from src.api.v1.routers.order import SideEnum
from src.celery_config import celery_app
from src.db.db import async_session_maker
from src.db.users import usersManager
from src.models import Orders, TradeLog
from src.models.orders import StatusEnum
from src.redis_conn import redis_client
from src.utils.redis_utils import match_limit_order

# странно баланс считаеться если покупать лимитно по меньшей стоимости чем в заявке, а остально вроде бы нормально
async def match_order_limit(orderOrm: Orders, ticker: str):
    try:
        r = await redis_client.get_redis()

        async with async_session_maker() as session:
            pipe = r.pipeline()
            userBalanceRUB = await usersManager.get_user_balance_by_ticker(
                session, orderOrm.user_uuid, 'RUB', create_if_missing=True
            )

            userBalanceTicker = await usersManager.get_user_balance_by_ticker(
                session, orderOrm.user_uuid, ticker=ticker, create_if_missing=True
            )
            total_cost, matched_orders, remaining_qty_order = await match_limit_order(r, ticker,
                                                                                      orderOrm.qty, orderOrm.price,
                                                                                      orderOrm.side.value)
            if matched_orders:
                orderOrm = await session.get(Orders, orderOrm.uuid)
                orderOrm.status = StatusEnum.EXECUTED if remaining_qty_order == 0 else StatusEnum.PARTIALLY_EXECUTED

                if orderOrm.side == SideEnum.SELL:
                    userBalanceRUB.available_balance += total_cost
                    userBalanceTicker.available_balance -= (orderOrm.qty - remaining_qty_order)

                else:
                    userBalanceRUB.available_balance -= total_cost
                    userBalanceTicker.available_balance += (orderOrm.qty - remaining_qty_order)

                if (userBalanceTicker.available_balance <= 0
                        and userBalanceTicker.frozen_balance <= 0):
                    await session.delete(userBalanceTicker)

                orderbook_key = f"orderbook:{ticker}:{'bids' if orderOrm.side == SideEnum.SELL else 'asks'}"

                if orderOrm.side == SideEnum.SELL:
                    for item in matched_orders:

                        buy_order_uuid = item.get("uuid")
                        price = item.get("price")
                        quantity = item.get("quantity")
                        total_cost = item.get("cost")
                        original_qty = item["original_qty"]

                        order_result = await session.execute(
                            select(Orders).where(Orders.uuid == buy_order_uuid)
                        )
                        buy_order = order_result.scalar_one()

                        buy_balance = await usersManager.get_user_balance_by_ticker(
                            session, buy_order.user_uuid, ticker=ticker, create_if_missing=True
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
                        )
                        session.add(trade)

                        # 4. Обновить статус ордера, если исполнен
                        buy_order.filled = (buy_order.filled or 0) + quantity
                        if buy_order.filled >= buy_order.qty:
                            buy_order.status = StatusEnum.EXECUTED
                        else:
                            buy_order.status = StatusEnum.PARTIALLY_EXECUTED

                        old_entry = f"{int(price)}:{int(original_qty)}:{buy_order_uuid}"
                        pipe.zrem(orderbook_key, old_entry)

                        remaining_qty = original_qty - quantity
                        if remaining_qty > 0:
                            new_entry = f"{int(price)}:{int(remaining_qty)}:{buy_order_uuid}"
                            pipe.zadd(orderbook_key, {new_entry: price})
                else:
                    for item in matched_orders:

                        sell_order_uuid = item.get("uuid")
                        price = item.get("price")
                        quantity = item.get("quantity")
                        cost = item.get("cost")
                        original_qty = item["original_qty"]



                        order_result = await session.execute(
                            select(Orders).where(Orders.uuid == sell_order_uuid)
                        )

                        sell_order = order_result.scalar_one()

                        sell_balance = await usersManager.get_user_balance_by_ticker(
                            session, sell_order.user_uuid, ticker=ticker, create_if_missing=True
                        )

                        rub_balance = await usersManager.get_user_balance_by_ticker(
                            session, sell_order.user_uuid, ticker="RUB", create_if_missing=True
                        )

                        rub_balance.available_balance += cost

                        sell_balance.frozen_balance -= quantity
                        # 3. Добавить в TradeLog
                        trade = TradeLog(
                            sell_order_id=sell_order.uuid,
                            buy_order_id=orderOrm.uuid,
                            price=price,
                            quantity=quantity,
                        )
                        session.add(trade)

                        # 4. Обновить статус ордера, если исполнен
                        sell_order.filled = (sell_order.filled or 0) + quantity
                        if sell_order.filled >= sell_order.qty:
                            sell_order.status = StatusEnum.EXECUTED
                        else:
                            sell_order.status = StatusEnum.PARTIALLY_EXECUTED

                        old_entry = f"{int(price)}:{int(original_qty)}:{sell_order_uuid}"
                        pipe.zrem(orderbook_key, old_entry)

                        remaining_qty = original_qty - quantity
                        if remaining_qty > 0:
                            new_entry = f"{int(price)}:{int(remaining_qty)}:{sell_order_uuid}"
                            pipe.zadd(orderbook_key, {new_entry: price})

            if orderOrm.side == SideEnum.SELL:
                userBalanceTicker.available_balance -= remaining_qty_order
                userBalanceTicker.frozen_balance += remaining_qty_order
            else:
                userBalanceRUB.available_balance -= remaining_qty_order * orderOrm.price
                userBalanceRUB.frozen_balance += remaining_qty_order * orderOrm.price

            orderbook_key_add = f"orderbook:{ticker}:{'asks' if orderOrm.side == SideEnum.SELL else 'bids'}"
            new_entry_add = f"{int(orderOrm.price)}:{int(remaining_qty_order)}:{orderOrm.uuid}"
            pipe.zadd(orderbook_key_add, {new_entry_add: orderOrm.price})

            await session.commit()
            await pipe.execute()

    except Exception as e:
        print(e)


@celery_app.task
async def match_order_limit2(order_id: str):
    print(f"Processing limit order: {order_id}")
