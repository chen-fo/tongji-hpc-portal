import posixpath
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.schemas.accounts import StrictModel
from app.security.dependencies import DB, AuthSession, CurrentUser
from app.services.files import FileIdentity, snapshot_identity

router = APIRouter(prefix="/api/files", tags=["files"])


class FilePath(StrictModel):
    path: str = Field(min_length=1, max_length=4096)


class Rename(FilePath):
    destination: str = Field(min_length=1, max_length=4096)


def identity(user: CurrentUser, session: AuthSession, db: DB):
    return snapshot_identity(db, user, session)


FileUser = Annotated[FileIdentity, Depends(identity)]


@router.get("")
async def listing(request: Request, user: FileUser, path: str = Query(default="", max_length=4096)):
    return await request.app.state.files.listing(user, path)


@router.post("/mkdir", status_code=204)
async def mkdir(data: FilePath, request: Request, user: FileUser):
    await request.app.state.files.mkdir(user, data.path)


@router.post("/rename", status_code=204)
async def rename(data: Rename, request: Request, user: FileUser):
    await request.app.state.files.rename(user, data.path, data.destination)


@router.delete("", status_code=204)
async def delete(
    request: Request, user: FileUser, path: str = Query(max_length=4096), recursive: bool = False
):
    await request.app.state.files.delete(user, path, recursive)


@router.put("/upload", status_code=201)
async def upload(
    request: Request, user: FileUser, path: str = Query(min_length=1, max_length=4096)
):
    value = request.headers.get("content-length")
    try:
        declared_size = int(value) if value is not None else None
    except ValueError:
        raise HTTPException(400, "无效的文件长度") from None
    return await request.app.state.files.upload(user, path, request.stream(), declared_size)


@router.get("/download")
async def download(
    request: Request, user: FileUser, path: str = Query(min_length=1, max_length=4096)
):
    source = await request.app.state.files.download(user, path)
    name = quote(posixpath.basename(path), safe="")
    return StreamingResponse(
        source.chunks(),
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(source.size),
            "Content-Disposition": f"attachment; filename=\"download\"; filename*=UTF-8''{name}",
        },
        background=BackgroundTask(source.close),
    )
