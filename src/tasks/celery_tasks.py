from src.celery_config import celery_app

# хотел делать через celery, но что то вообще не пошло
@celery_app.task
def match_order_limit2(order_id: str):
    print(f"Processing limit order: {order_id}")