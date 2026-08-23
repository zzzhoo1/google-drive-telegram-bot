import asyncio
import inspect
import json
import logging
import os
import re
import threading
import time
import urllib.parse as urlparse
from io import BytesIO
from mimetypes import guess_type
from typing import Any, Callable, Optional
from urllib.parse import parse_qs

from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from tenacity import RetryError, Retrying, before_log, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_random_exponential

from bot import LOGGER, SERVICE_ACCOUNT_GRANT_ACCESS
from bot.config import Messages
from bot.helpers.sql_helper import gDriveDB
from bot.helpers.utils import format_bytes, humanbytes


class AdaptiveChunkController:
    _BASE_UNIT = 256 * 1024

    def __init__(self, min_size=8 * 1024 * 1024, max_size=32 * 1024 * 1024, step=4 * 1024 * 1024):
        self._min = self._align_value(min_size)
        self._max = self._align_value(max_size)
        self._step = max(self._BASE_UNIT, self._align_value(step))
        self._current = max(self._min, min(self._align_value(min_size), self._max))
        self._success_streak = 0
        self._failure_streak = 0

    def _align_value(self, value: int) -> int:
        value = max(value, self._BASE_UNIT)
        return (value // self._BASE_UNIT) * self._BASE_UNIT

    @property
    def current_size(self) -> int:
        return self._current

    def _set_current(self, value: int) -> None:
        self._current = max(self._min, min(self._align_value(value), self._max))

    def apply_to(self, media) -> None:
        media.chunksize = self._align_value(self._current)

    def record_success(self) -> int:
        self._success_streak += 1
        self._failure_streak = 0
        if self._success_streak >= 2 and self._current < self._max:
            self._set_current(self._current + self._step)
            self._success_streak = 0
        return self._current

    def record_failure(self) -> int:
        self._failure_streak += 1
        self._success_streak = 0
        if self._failure_streak >= 1 and self._current > self._min:
            self._set_current(self._current - self._step)
            self._failure_streak = 0
        return self._current


logging.getLogger("googleapiclient.discovery").setLevel(logging.ERROR)


class GoogleDrive:
    def __init__(
        self,
        *,
        user_id: int,
        credentials,
        parent_id: Optional[str],
        mode: str,
        fingerprint: Optional[str],
    ) -> None:
        self.__G_DRIVE_DIR_MIME_TYPE = "application/vnd.google-apps.folder"
        self.__G_DRIVE_BASE_DOWNLOAD_URL = "https://drive.google.com/uc?id={}&export=download"
        self.__G_DRIVE_DIR_BASE_DOWNLOAD_URL = "https://drive.google.com/drive/folders/{}"
        self._user_id = user_id
        self._mode = mode
        self._fingerprint = fingerprint
        self.__parent_id = parent_id or "root"
        self.__service = self.authorize(credentials)
        self._retryer = self._build_retryer()
        self._active_chunk_controller: Optional[AdaptiveChunkController] = None
        if self._mode == "service_account" and SERVICE_ACCOUNT_GRANT_ACCESS:
            self._ensure_service_account_permissions(credentials)

    def _build_retryer(self) -> Retrying:
        return Retrying(
            wait=wait_random_exponential(multiplier=2, max=30),
            stop=stop_after_attempt(5),
            retry=retry_if_exception(self._should_retry_exception),
            before=before_log(LOGGER, logging.DEBUG),
            reraise=True,
        )

    def _should_retry_exception(self, exc: BaseException) -> bool:
        if isinstance(exc, HttpError):
            status = getattr(exc.resp, "status", None)
            if status in (401, 403, 429):
                return True
            if status and status >= 500:
                return True
            try:
                details = json.loads(exc.content).get("error", {}).get("errors", []) if exc.content else []
            except Exception:
                details = []
            for item in details:
                reason = item.get("reason")
                if reason in {"rateLimitExceeded", "userRateLimitExceeded", "backendError", "dailyLimitExceeded"}:
                    return True
        if isinstance(exc, (RefreshError, TransportError)):
            return True
        return False

    def _record_failure(self, exc: BaseException) -> None:
        if self._should_retry_exception(exc):
            gDriveDB.mark_failure(self._user_id)

    def _reset_failures(self) -> None:
        gDriveDB.reset_failures(self._user_id)

    def _start_upload_session(self) -> AdaptiveChunkController:
        controller = AdaptiveChunkController()
        self._active_chunk_controller = controller
        return controller

    def _finish_upload_session(self) -> None:
        self._active_chunk_controller = None

    def _call(self, func: Callable[[], Any]):
        try:
            result = self._retryer(func)
            self._reset_failures()
            return result
        except RetryError as err:
            exc = err.last_attempt.exception()
            if exc:
                self._record_failure(exc)
                raise exc
            raise
        except Exception as exc:
            self._record_failure(exc)
            raise

    def _wait_if_paused(
        self,
        pause_event: Optional[threading.Event],
        cancel_callback: Optional[Callable[[], bool]],
    ) -> None:
        if pause_event is None:
            return
        while not pause_event.is_set():
            if cancel_callback and cancel_callback():
                raise RuntimeError("cancelled")
            time.sleep(0.2)

    def _perform_chunked_upload(
        self,
        request,
        controller: Optional[AdaptiveChunkController],
        *,
        on_progress: Optional[Callable[[int], None]] = None,
        pause_event: Optional[threading.Event] = None,
        cancel_callback: Optional[Callable[[], bool]] = None,
    ):
        controller = controller or self._active_chunk_controller
        if controller is None:
            raise RuntimeError("Missing upload controller")
        media = getattr(request, "resumable", None) or getattr(request, "resumable_media", None)
        if media is None:
            media = getattr(request, "media_body", None)
        if media is None:
            raise RuntimeError("Invalid upload request")
        controller.apply_to(media)
        response = None
        while response is None:
            if cancel_callback and cancel_callback():
                raise RuntimeError("cancelled")
            self._wait_if_paused(pause_event, cancel_callback)

            # Proactive patch: Fix request.resumable.chunksize before calling next_chunk
            if hasattr(request, "resumable"):
                resumable = getattr(request, "resumable", None)
                if resumable and hasattr(resumable, "chunksize") and not callable(
                    resumable.chunksize
                ):
                    original_size = resumable.chunksize
                    LOGGER.warning(
                        "Proactive patch: request.resumable.chunksize=%d is not callable, fixing...",
                        original_size,
                    )
                    resumable.chunksize = lambda: original_size
                    LOGGER.info("request.resumable.chunksize patched to callable")

            try:
                # Workaround for google-api-python-client MediaFileUpload chunksize issue
                # The issue: MediaFileUpload.chunksize is set as an int instead of callable
                # Reference: https://github.com/googleapis/google-api-python-client/issues/1825
                try:
                    status, response = request.next_chunk()
                except TypeError as te:
                    if "'int' object is not callable" in str(te) and "chunksize" in str(te):
                        LOGGER.warning(
                            "Detected MediaFileUpload.chunksize API issue, applying workaround..."
                        )
                        media = getattr(request, "media_body", None)
                        if media and hasattr(media, "_chunksize"):
                            # Ensure _chunksize is int
                            if not isinstance(media._chunksize, int):
                                LOGGER.warning(
                                    "Converting MediaFileUpload._chunksize from %s to int",
                                    type(media._chunksize).__name__,
                                )
                                media._chunksize = int(media._chunksize)
                            else:
                                LOGGER.info(
                                    "MediaFileUpload._chunksize is already int: %d", media._chunksize
                                )

                            # Compatibility fix: Make .chunksize a callable lambda
                            if hasattr(media, "chunksize") and not callable(media.chunksize):
                                LOGGER.warning(
                                    "Fixing media.chunksize from int (%d) to lambda returning int.",
                                    media.chunksize,
                                )
                                media.chunksize = lambda: media._chunksize

                            LOGGER.info("Chunksize workaround applied, retrying next_chunk()...")
                        # Retry the operation after patching
                        status, response = request.next_chunk()
                    else:
                        raise
                controller.record_success()
                if cancel_callback and cancel_callback():
                    raise RuntimeError("cancelled")
                if status and on_progress:
                    on_progress(int(status.resumable_progress))
                if response is None:
                    controller.apply_to(media)
            except Exception as exc:
                if isinstance(exc, RuntimeError) and str(exc) == "cancelled":
                    raise
                controller.record_failure()
                controller.apply_to(media)
                raise
        return response

    def _ensure_service_account_permissions(self, credentials) -> None:
        if self.__parent_id == "root":
            return
        email = getattr(credentials, "service_account_email", None)
        if not email:
            return
        body = {
            "type": "user",
            "role": "writer",
            "emailAddress": email,
        }
        try:
            self._call(
                lambda: self.__service.permissions()
                .create(
                    fileId=self.__parent_id,
                    body=body,
                    supportsAllDrives=True,
                    sendNotificationEmail=False,
                )
                .execute()
            )
        except HttpError as err:
            try:
                payload = json.loads(err.content)
                reason = payload.get("error", {}).get("errors", [{}])[0].get("reason")
            except Exception:
                reason = None
            if reason != "alreadyExists":
                LOGGER.warning("Failed to grant service account access to %s: %s", self.__parent_id, err)

    def getIdFromUrl(self, link: str):
        if "folders" in link or "file" in link:
            regex = r"https://drive\.google\.com/(drive)?/?u?/?\d?/?(mobile)?/?(file)?(folders)?/?d?/([-\w]+)[?+]?/?(w+)?"
            res = re.search(regex, link)
            if res is None:
                raise IndexError("GDrive ID not found.")
            return res.group(5)
        parsed = urlparse.urlparse(link)
        return parse_qs(parsed.query)["id"][0]

    def search_files(self, query, page_token=None):
        sanitized = query.replace("'", "\\'")
        params = {
            "q": f"name contains '{sanitized}'",
            "spaces": "drive",
            "corpora": "allDrives",
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
            "pageSize": 20,
            "fields": "nextPageToken, files(id, name, mimeType, size)",
        }
        if page_token:
            params["pageToken"] = page_token
        return self._call(lambda: self.__service.files().list(**params).execute())

    def getFilesByFolderId(self, folder_id):
        page_token = None
        q = f"'{folder_id}' in parents"
        files = []
        while True:
            params = {
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
                "q": q,
                "spaces": "drive",
                "pageSize": 200,
                "fields": "nextPageToken, files(id, name, mimeType,size)",
            }
            if page_token:
                params["pageToken"] = page_token
            response = self._call(lambda: self.__service.files().list(**params).execute())
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if page_token is None:
                break
        return files

    def copyFile(self, file_id, dest_id):
        body = {"parents": [dest_id]}
        try:
            return self._call(
                lambda: self.__service.files()
                .copy(supportsAllDrives=True, fileId=file_id, body=body)
                .execute()
            )
        except HttpError as err:
            if err.resp.get("content-type", "").startswith("application/json"):
                reason = json.loads(err.content).get("error", {}).get("errors", [{}])[0].get("reason")
                if reason == "dailyLimitExceeded":
                    raise IndexError("LimitExceeded")
            raise

    def cloneFolder(self, name, local_path, folder_id, parent_id):
        files = self.getFilesByFolderId(folder_id)
        new_id = None
        if len(files) == 0:
            return self.__parent_id
        for file in files:
            if file.get("mimeType") == self.__G_DRIVE_DIR_MIME_TYPE:
                file_path = os.path.join(local_path, file.get("name"))
                current_dir_id = self.create_directory(file.get("name"))
                new_id = self.cloneFolder(file.get("name"), file_path, file.get("id"), current_dir_id)
            else:
                try:
                    self.transferred_size += int(file.get("size"))
                except (TypeError, ValueError):
                    pass
                try:
                    self.copyFile(file.get("id"), parent_id)
                    new_id = parent_id
                except Exception as err:
                    return err
        return new_id

    def create_directory(self, directory_name):
        file_metadata = {
            "name": directory_name,
            "mimeType": self.__G_DRIVE_DIR_MIME_TYPE,
            "parents": [self.__parent_id],
        }
        file = self._call(
            lambda: self.__service.files()
            .create(supportsAllDrives=True, body=file_metadata)
            .execute()
        )
        return file.get("id")

    def clone(self, link):
        self.transferred_size = 0
        try:
            file_id = self.getIdFromUrl(link)
        except (IndexError, KeyError):
            return Messages.INVALID_GDRIVE_URL
        try:
            meta = self._call(
                lambda: self.__service.files()
                .get(
                    supportsAllDrives=True,
                    fileId=file_id,
                    fields="name,id,mimeType,size",
                )
                .execute()
            )
            if meta.get("mimeType") == self.__G_DRIVE_DIR_MIME_TYPE:
                dir_id = self.create_directory(meta.get("name"))
                result = self.cloneFolder(meta.get("name"), meta.get("name"), meta.get("id"), dir_id)
                return Messages.COPIED_SUCCESSFULLY.format(
                    meta.get("name"),
                    self.__G_DRIVE_DIR_BASE_DOWNLOAD_URL.format(dir_id),
                    humanbytes(self.transferred_size),
                )
            file = self.copyFile(meta.get("id"), self.__parent_id)
            return Messages.COPIED_SUCCESSFULLY.format(
                file.get("name"),
                self.__G_DRIVE_BASE_DOWNLOAD_URL.format(file.get("id")),
                humanbytes(int(meta.get("size", 0))),
            )
        except Exception as err:
            if isinstance(err, RetryError):
                LOGGER.info("Total Attempts: %s", err.last_attempt.attempt_number)
                err = err.last_attempt.exception()
            err = str(err).replace(">", "").replace("<", "")
            LOGGER.error(err)
            return f"**ERROR:** ```{err}```"

    def upload_file(self, file_path, mimeType=None):
        if ".." in file_path:
            raise Exception("Invalid file path")
        mime_type = mimeType if mimeType else guess_type(file_path)[0]
        mime_type = mime_type if mime_type else "text/plain"
        controller = self._start_upload_session()
        filename = os.path.basename(file_path)
        filesize = humanbytes(os.path.getsize(file_path))
        body = {
            "name": filename,
            "description": "Uploaded using @UploadGdriveBot",
            "mimeType": mime_type,
            "parents": [self.__parent_id],
        }
        LOGGER.info("Upload: %s", file_path)
        try:
            # 确保 chunksize 是有效的整数
            chunk_size = controller.current_size
            if not isinstance(chunk_size, int) or chunk_size <= 0:
                LOGGER.warning(
                    "Invalid chunksize: %r (type=%s), using default 5MB",
                    chunk_size,
                    type(chunk_size).__name__,
                )
                chunk_size = 5 * 1024 * 1024  # 5MB default

            LOGGER.info("upload_file: Using chunksize=%d bytes", chunk_size)

            # Use BytesIO to avoid MediaFileUpload chunksize bug
            LOGGER.info("Using BytesIO upload to avoid MediaFileUpload bug")
            try:
                with open(file_path, "rb") as f:
                    file_content = f.read()

                media_body = MediaIoBaseUpload(
                    BytesIO(file_content),
                    mimetype=mime_type,
                    chunksize=chunk_size,
                    resumable=True,
                )
                LOGGER.info("BytesIO media_body created successfully")

                # Critical fix: Monkey patch chunksize to be callable
                if hasattr(media_body, "_chunksize"):
                    original_chunksize = media_body._chunksize
                    LOGGER.info("Patching media_body._chunksize=%d to callable", original_chunksize)
                    media_body._chunksize = original_chunksize
                    media_body.chunksize = lambda: original_chunksize
                    LOGGER.info("Chunksize patched successfully")

            except Exception as e:
                LOGGER.error("BytesIO upload failed, falling back: %s", str(e), exc_info=True)
                media_body = MediaFileUpload(
                    file_path,
                    mimetype=mime_type,
                    chunksize=chunk_size,
                    resumable=True,
                )
            request = self.__service.files().create(
                body=body,
                media_body=media_body,
                fields="id",
                supportsAllDrives=True,
            )

            # Patch request.resumable.chunksize to be callable
            if hasattr(request, "resumable") and request.resumable:
                resumable = request.resumable
                if hasattr(resumable, "chunksize") and not callable(resumable.chunksize):
                    original_size = resumable.chunksize
                    LOGGER.info(
                        "Patching request.resumable.chunksize=%d to callable", original_size
                    )
                    resumable.chunksize = lambda: original_size
                    LOGGER.info("request.resumable.chunksize patched successfully")

            def perform():
                return self._perform_chunked_upload(request, controller)

            uploaded_file = self._call(perform)

            # 类型检查：确保返回值是字典
            if not isinstance(uploaded_file, dict):
                LOGGER.error(
                    "upload_file: Invalid response from _call. expected=dict, got=%s, value=%r",
                    type(uploaded_file).__name__,
                    uploaded_file,
                )
                raise RuntimeError(
                    f"Invalid upload response: expected dict, got {type(uploaded_file).__name__}"
                )

            file_id = uploaded_file.get("id")
            if not file_id:
                LOGGER.error("upload_file: No file_id in response: %r", uploaded_file)
                raise RuntimeError("Upload response missing file_id")

            return Messages.UPLOADED_SUCCESSFULLY.format(
                filename=filename,
                link=self.__G_DRIVE_BASE_DOWNLOAD_URL.format(file_id),
                size=filesize,
            )
        except HttpError as err:
            LOGGER.error("upload_file: HttpError: %s", str(err), exc_info=True)
            if err.resp.get("content-type", "").startswith("application/json"):
                reason = json.loads(err.content).get("error", {}).get("errors", [{}])[0].get("reason")
                if reason in {"userRateLimitExceeded", "dailyLimitExceeded"}:
                    return Messages.RATE_LIMIT_EXCEEDED_MESSAGE
                return f"**ERROR:** {reason}"
            return f"**ERROR:** ``````"
        except Exception as e:
            LOGGER.error("upload_file: Unexpected exception: %s", str(e), exc_info=True)
            return f"**ERROR:** ``````"
        finally:
            self._finish_upload_session()

    async def upload_file_with_progress(
        self,
        file_path,
        mimeType=None,
        progress_callback=None,
        pause_event: threading.Event = None,
        cancel_callback=None,
    ):
        mime_type = mimeType if mimeType else guess_type(file_path)[0]
        mime_type = mime_type if mime_type else "text/plain"
        controller = self._start_upload_session()
        filename = os.path.basename(file_path)
        total_size = os.path.getsize(file_path)
        body = {
            "name": filename,
            "description": "Uploaded using @UploadGdriveBot",
            "mimeType": mime_type,
            "parents": [self.__parent_id],
        }
        loop = asyncio.get_running_loop()
        media_body = MediaFileUpload(
            file_path,
            mimetype=mime_type,
            chunksize=controller.current_size,
            resumable=True,
        )
        request = self.__service.files().create(
            body=body,
            media_body=media_body,
            fields="id",
            supportsAllDrives=True,
        )

        async def notify(progress):
            if not progress_callback:
                return
            if inspect.iscoroutinefunction(progress_callback):
                await progress_callback(progress, total_size)
            else:
                progress_callback(progress, total_size)

        def dispatch(progress):
            if not progress_callback:
                return
            if inspect.iscoroutinefunction(progress_callback):
                asyncio.run_coroutine_threadsafe(progress_callback(progress, total_size), loop)
            else:
                loop.call_soon_threadsafe(progress_callback, progress, total_size)

        def perform():
            return self._perform_chunked_upload(
                request,
                controller,
                on_progress=dispatch,
                pause_event=pause_event,
                cancel_callback=cancel_callback,
            )

        def wrapped_call():
            """包装 _call 以确保返回值正确"""
            result = self._call(perform)
            if not isinstance(result, dict):
                LOGGER.warning(
                    "_call returned non-dict: type=%s, value=%r",
                    type(result).__name__,
                    result,
                )
                raise RuntimeError(
                    f"Invalid upload response: expected dict, got {type(result).__name__}"
                )
            return result

        async def run_upload():
            try:
                return await loop.run_in_executor(None, wrapped_call)
            except Exception as exc:
                raise exc

        try:
            await notify(0)
            uploaded_file = await run_upload()
            await notify(total_size)
            file_id = uploaded_file.get("id")
            filesize = format_bytes(total_size)
            return Messages.UPLOADED_SUCCESSFULLY.format(
                filename=filename,
                link=self.__G_DRIVE_BASE_DOWNLOAD_URL.format(file_id),
                size=filesize,
            )
        except RetryError as err:
            LOGGER.info("Total Attempts: %s", err.last_attempt.attempt_number)
            error = err.last_attempt.exception()
            LOGGER.error("RetryError occurred: %s", str(error), exc_info=True)
            if isinstance(error, HttpError) and error.resp.get("content-type", "").startswith("application/json"):
                reason = json.loads(error.content).get("error", {}).get("errors", [{}])[0].get("reason")
                if reason in {"userRateLimitExceeded", "dailyLimitExceeded"}:
                    return Messages.RATE_LIMIT_EXCEEDED_MESSAGE
                return f"**ERROR:** {reason}"
            return f"**ERROR:** ``````"
        except HttpError as err:
            LOGGER.error("HttpError occurred: %s", str(err), exc_info=True)
            if err.resp.get("content-type", "").startswith("application/json"):
                reason = json.loads(err.content).get("error", {}).get("errors", [{}])[0].get("reason")
                if reason in {"userRateLimitExceeded", "dailyLimitExceeded"}:
                    return Messages.RATE_LIMIT_EXCEEDED_MESSAGE
                return f"**ERROR:** {reason}"
            return f"**ERROR:** ``````"
        except Exception as e:
            LOGGER.error("Unexpected exception in upload_file_with_progress: %s", str(e), exc_info=True)
            LOGGER.error("Exception type: %s, Exception message: %s", type(e).__name__, str(e))
            return f"**ERROR:** ``````"
        finally:
            self._finish_upload_session()

    def checkFolderLink(self, link: str):
        try:
            file_id = self.getIdFromUrl(link)
        except (IndexError, KeyError):
            raise IndexError
        try:
            file = self._call(
                lambda: self.__service.files()
                .get(supportsAllDrives=True, fileId=file_id, fields="mimeType")
                .execute()
            )
        except HttpError as err:
            if err.resp.get("content-type", "").startswith("application/json"):
                reason = json.loads(err.content).get("error", {}).get("errors", [{}])[0].get("reason")
                if "notFound" in reason:
                    return False, Messages.FILE_NOT_FOUND_MESSAGE.format(file_id)
                return False, f"**ERROR:** ```{str(err).replace('>', '').replace('<', '')}```"
            raise
        if str(file.get("mimeType")) == self.__G_DRIVE_DIR_MIME_TYPE:
            return True, file_id
        return False, Messages.NOT_FOLDER_LINK

    def delete_file(self, link: str):
        try:
            file_id = self.getIdFromUrl(link)
        except (IndexError, KeyError):
            return Messages.INVALID_GDRIVE_URL
        try:
            self._call(
                lambda: self.__service.files().delete(fileId=file_id, supportsAllDrives=True).execute()
            )
            return Messages.DELETED_SUCCESSFULLY.format(file_id)
        except HttpError as err:
            if err.resp.get("content-type", "").startswith("application/json"):
                reason = json.loads(err.content).get("error", {}).get("errors", [{}])[0].get("reason")
                if "notFound" in reason:
                    return Messages.FILE_NOT_FOUND_MESSAGE.format(file_id)
                if "insufficientFilePermissions" in reason:
                    return Messages.INSUFFICIENT_PERMISSONS.format(file_id)
                return f"**ERROR:** ```{str(err).replace('>', '').replace('<', '')}```"
            raise

    def emptyTrash(self):
        try:
            self._call(lambda: self.__service.files().emptyTrash().execute())
            return Messages.EMPTY_TRASH
        except HttpError as err:
            return f"**ERROR:** ```{str(err).replace('>', '').replace('<', '')}```"

    def authorize(self, creds):
        return build("drive", "v3", credentials=creds, cache_discovery=False)
