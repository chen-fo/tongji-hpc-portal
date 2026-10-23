from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/ws/terminal")
async def terminal(ws: WebSocket):
    await ws.app.state.terminals.handle(ws)
