from datetime import datetime

from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks, status
from pydantic import BaseModel, Field, ConfigDict, UUID4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.db import get_async_session
from src.db.users import usersManager
from src.models import Orders, TradeLog, Users
from src.models.orders import TypeEnum, SideEnum, StatusEnum
from src.redis_conn import redis_client
from src.tasks.orders import match_order_limit, add_tradeLog_redis
from src.utils.redis_utils import check_ticker_exists, calculate_order_cost
from src.tasks.celery_tasks import match_order_limit2

router = APIRouter(prefix="/order", tags=["orders"])


class OrderBase(BaseModel):
    direction: SideEnum
    qty: int = Field(..., ge=1)
    ticker: str = Field(..., pattern='^[A-Z]{2,10}$')


class MarketOrder(OrderBase):
    model_config = ConfigDict(extra="forbid")


class LimitOrder(OrderBase):
    price: int = Field(..., gt=0)


async def create_orderOrm(user, session, instrument_id, order_data):
    orders = Orders(
        user_uuid=user.id,
        instrument_id=instrument_id,
        order_type=TypeEnum.MARKET_ORDER if isinstance(order_data, MarketOrder) else TypeEnum.LIMIT_ORDER,
        side=SideEnum.BUY if order_data.direction.value == "BUY" else SideEnum.SELL,
        qty=order_data.qty,
        status=StatusEnum.EXECUTED if isinstance(order_data, MarketOrder) else StatusEnum.NEW,
        price=order_data.price if isinstance(order_data, LimitOrder) else None,
        filled=None if isinstance(order_data, MarketOrder) else 0,
    )
    session.add(orders)
    await session.flush()
    await session.refresh(orders)
    return orders


class Body(BaseModel):
    direction: SideEnum = Field(validation_alias='side')
    ticker: str = Field(..., pattern='^[A-Z]{2,10}$')
    qty: int = Field(..., ge=1)
    price: int | None = None
    model_config = ConfigDict(
        exclude_none=True
    )


class GetOrder(BaseModel):
    id: UUID4 = Field(validation_alias='uuid')
    status: StatusEnum
    user_id: UUID4 = Field(validation_alias='user_uuid')
    timestamp: datetime = Field(validation_alias='create_at')
    body: Body
    filled: int | None = Field(None, ge=0)
    model_config = ConfigDict(
        exclude_none=True
    )


def create_GetOrder(orderOrm):
    body = Body.model_validate(orderOrm, from_attributes=True)
    order = GetOrder.model_validate(
        {
            **orderOrm.__dict__,
            "body": body
        },
        from_attributes=True
    )
    return order


@router.get('/{order_id}')
async def get_order(request: Request,
                    order_id: UUID4,
                    session: AsyncSession = Depends(get_async_session)):
    orderOrm = (await session.execute(
        select(Orders).options(selectinload(Orders.instrument)).where(Orders.uuid == order_id,
                                                                      Orders.user_uuid == request.state.user.id)
    )).scalars().one_or_none()
    if not orderOrm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Order not found")

    order = create_GetOrder(orderOrm)
    return order.model_dump(exclude_none=True)


@router.delete('/{order_id}')
async def cancel_order(request: Request,
                       order_id: UUID4, session: AsyncSession = Depends(get_async_session)):
    orderOrm = (await session.execute(
        select(Orders).options(selectinload(Orders.instrument)).where(Orders.uuid == order_id, )
    )).scalar_one_or_none()

    if not orderOrm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if str(orderOrm.user_uuid) != str(request.state.user.id) and request.state.user.role != "ADMIN":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    if orderOrm.status in {StatusEnum.EXECUTED, StatusEnum.CANCELLED}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

    key = f"{int(orderOrm.price)}:{int(orderOrm.qty - orderOrm.filled)}:{orderOrm.uuid}"
    orderbook_key = f"orderbook:{orderOrm.ticker}:{'asks' if orderOrm.side == SideEnum.SELL else 'bids'}"

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

    r = await redis_client.get_redis()
    pipe = r.pipeline()
    pipe.zrem(orderbook_key, key)
    await pipe.execute()

    orderOrm.status = StatusEnum.CANCELLED
    await session.commit()

    return {"success": True}


@router.get('')
async def get_list_orders(request: Request,
                          session: AsyncSession = Depends(get_async_session)):
    user = request.state.user
    user = (await session.execute(
        select(Users).options(selectinload(Users.orders).selectinload(Orders.instrument)).where(Users.uuid == user.id)
    )).scalars().one()
    return [create_GetOrder(order).model_dump(exclude_none=True) for order in user.orders]


# фоновые задачи будут в celery, но пока в background_tasks


@router.post("")
async def create_order(request: Request, background_tasks: BackgroundTasks,
                       order_data: LimitOrder | MarketOrder,
                       session: AsyncSession = Depends(get_async_session)):
    r = await redis_client.get_redis()
    user = request.state.user
    match_order_limit2.delay(order_data.ticker)
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
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    else:
        if isinstance(order_data, MarketOrder):
            # при рыночном пытаемся собрать самую дешёвую покупку и проверяем от этого его баланс
            try:
                total_cost, matched_orders = await calculate_order_cost(r, order_data.ticker,
                                                                        order_data.qty, order_data.direction.value)
            except ValueError as e:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
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

    orderOrm = await create_orderOrm(user, session, instrument_id, order_data)
    if isinstance(order_data, MarketOrder):
        orderbook_key = f"orderbook:{order_data.ticker}:{'bids' if order_data.direction == SideEnum.SELL else 'asks'}"
        # рыночная продажа
        if order_data.direction == SideEnum.SELL:
            userBalanceRub.available_balance += total_cost
            userBalanceTicker.available_balance -= order_data.qty
            # if (userBalanceTicker.available_balance <= 0
            #         and userBalanceTicker.frozen_balance <= 0):
            #     await session.delete(userBalanceTicker)

            # в этом же форе обновлять кеш
            pipe = r.pipeline()

            for item in matched_orders:

                buy_order_uuid = item.get("uuid")
                price = item.get("price")
                quantity = item.get("quantity")
                total_cost = item.get("cost")
                original_qty = item.get("original_qty")

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
                await session.flush()
                add_tradeLog_redis(pipe, order_data.ticker, {
                    "ticker": order_data.ticker,
                    "amount": quantity,
                    "price": price,
                    "timestamp": trade.create_at.isoformat(),
                })

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

            await session.commit()
            await pipe.execute()
        else:  # order_data.direction == Direction.BUY

            userBalanceRub.available_balance -= total_cost
            userBalanceTicker.available_balance += orderOrm.qty

            # if (userBalanceTicker.available_balance <= 0
            #         and userBalanceTicker.frozen_balance <= 0):
            #     await session.delete(userBalanceTicker)

            pipe = r.pipeline()

            for item in matched_orders:

                sell_order_uuid = item.get("uuid")
                price = item.get("price")
                quantity = item.get("quantity")
                total_cost = item.get("cost")
                original_qty = item["original_qty"]

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
                await session.flush()
                add_tradeLog_redis(pipe, order_data.ticker, {
                    "ticker": order_data.ticker,
                    "amount": quantity,
                    "price": price,
                    "timestamp": trade.create_at.isoformat(),
                })

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

            await session.commit()
            await pipe.execute()

    else:
        try:

            background_tasks.add_task(match_order_limit, orderOrm, order_data.ticker)
            await session.commit()
        except Exception as e:
            print(e)

    return {"order_id": orderOrm.uuid,
            "success": True}
