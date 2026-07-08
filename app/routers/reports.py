from fastapi import APIRouter, HTTPException, Response

from app.services.report_builder import ReportNotFoundError, get_report_builder

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/download/{token}")
def download_report(token: str) -> Response:
    try:
        content, filename, content_type = get_report_builder().get(token)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
