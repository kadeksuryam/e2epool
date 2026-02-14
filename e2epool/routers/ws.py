import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from e2epool.database import SessionLocal
from e2epool.dependencies import get_inventory, verify_ws_token
from e2epool.schemas import WSRequest
from e2epool.services.ws_handler import handle_message
from e2epool.services.ws_manager import ws_manager

logger = structlog.get_logger()

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/agent")
async def ws_agent(
    websocket: WebSocket,
    runner_id: str = Query(...),
    token: str = Query(...),
):
    inventory = get_inventory()
    try:
        runner = verify_ws_token(runner_id, token, inventory)
    except ValueError:
        await websocket.close(code=4401, reason="Invalid credentials")
        return

    await websocket.accept()
    await ws_manager.connect(runner_id, websocket)
    logger.info("WS agent connected", runner_id=runner_id)

    try:
        while True:
            raw = await websocket.receive_json()

            # Agent responses to controller-initiated exec commands
            if "status" in raw:
                ws_manager.route_response(raw.get("id", ""), raw)
                continue

            try:
                request = WSRequest.model_validate(raw)
            except ValidationError as e:
                await websocket.send_json(
                    {
                        "id": raw.get("id", ""),
                        "status": "error",
                        "error": {"code": 400, "detail": str(e)},
                    }
                )
                continue

            db = SessionLocal()
            try:
                response = handle_message(request, runner, db)
            finally:
                db.close()

            await websocket.send_json(response.model_dump(mode="json"))
    except WebSocketDisconnect:
        logger.info("WS agent disconnected", runner_id=runner_id)
    except Exception:
        logger.exception("WS error", runner_id=runner_id)
    finally:
        await ws_manager.disconnect(runner_id)
