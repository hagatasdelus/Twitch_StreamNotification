import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, NoReturn

from regular_execution.logger import get_logger
from regular_execution.stream_status import StreamStatus
from regular_execution.twitch import TwitchAPI, TwitchAPIError

logger = get_logger(__name__)

def get_base_path() -> Path:
    """実行ファイルのベースパスを取得"""
    if "__compiled__" in globals():
        return Path(os.path.dirname(os.path.realpath(sys.argv[0])))
    return Path(__file__).parent.resolve()

class NotificationError(Exception):
    """通知関連の例外"""

class StreamNotificationApp:
    def __init__(self):
        self.base_dir = get_base_path()
        self.twitch_api = TwitchAPI()
        self.is_running = True
        self._cleanup_tasks: list[asyncio.Task] = []

    @asynccontextmanager
    async def initialize(self) -> AsyncIterator["StreamNotificationApp"]:
        """アプリケーションの初期化とクリーンアップを管理"""
        await self.twitch_api.initialize()
        yield self

    def _handle_script_not_found(self, script_path: Path) -> NoReturn:
        """スクリプトが見つからない場合のエラー処理"""
        error_msg = f"Script not found: {script_path}"
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)

    async def display_message(self, message: str) -> None:
        """メッセージを非同期に表示"""
        print(message)
        await asyncio.sleep(0)  # イベントループに制御を戻す

    def format_display_message(self, username: str, display_name: str, stream_title: str) -> str:
        """表示メッセージのフォーマット"""
        base_format = f" has started streaming: {stream_title}"
        if username.lower() == display_name.lower():
            return display_name + base_format
        return f"{display_name}({username})" + base_format

    async def _run_notification_script(self, message: str, title: str) -> None:
        """通知用のAppleScriptを非同期に実行"""
        try:
            script_path = self.base_dir / "applescript" / "notification.applescript"
            if not script_path.exists():
                self._handle_script_not_found(script_path)

            cmd = ["/usr/bin/osascript", str(script_path), message, title]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()

        except (subprocess.SubprocessError, FileNotFoundError, NotificationError):
            logger.exception("Notification failed")
            await self.display_message("Failed to send notification")

    async def check_stream_status(self, username: str) -> None:
        """配信状態を定期的にチェック"""
        check_interval = 60  # 通常の確認間隔（秒）
        streaming_interval = 3600  # 配信中の確認間隔（秒）

        while self.is_running:
            try:
                display_name, stream_title = await self.twitch_api.get_stream_by_name(username)
                is_streaming = stream_title is not None
                status = StreamStatus.STREAMING if is_streaming else StreamStatus.NOTSTREAMING

                logger.info(
                    "Checking stream status: %s - %s",
                    username,
                    status.value.title()
                )

                if display_name and stream_title:
                    message = self.format_display_message(username, display_name, stream_title)
                    await self._run_notification_script(message, "Stream Started")
                    await self.display_message(message)
                    await asyncio.sleep(streaming_interval)
                else:
                    await asyncio.sleep(check_interval)

            except TwitchAPIError:
                logger.exception("Failed to check stream status")
                await asyncio.sleep(check_interval)
            except Exception:
                logger.exception("Unexpected error while checking stream status")
                await asyncio.sleep(check_interval)

    async def check_streamer_existence(self, username: str) -> bool:
        """ストリーマーの存在確認"""
        try:
            await self.display_message("Please wait a moment.")

            broadcaster_id = await self.twitch_api.get_broadcaster_id(username)
            if broadcaster_id:
                message = f"{username} found. You will be notified when the streaming starts."
                await self._run_notification_script(message, "Streamer Found")
                await self.display_message(message)
                return True

            message = f"{username} not found."
            await self.display_message(message)
            return False

        except TwitchAPIError:
            logger.exception("Failed to check streamer existence")
            await self.display_message("Failed to check streamer existence.")
            return False
        except Exception:
            logger.exception("Unexpected error while checking streamer")
            await self.display_message("An unexpected error occurred.")
            return False

    async def cleanup(self) -> None:
        """アプリケーションのクリーンアップ処理"""
        if not self.is_running:
            return
        logger.info("Starting application cleanup...")
        self.is_running = False

        # 実行中のタスクをキャンセル
        for task in self._cleanup_tasks:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # Twitchクライアントのクリーンアップ
        await self.twitch_api.close()
        logger.info("Application cleanup completed")

    def handle_signal(self, _sig: int, _frame: object | None) -> None:
        """シグナルハンドラ"""
        print("\nPlease wait a moment, terminating the application...")
        loop = asyncio.get_event_loop()
        loop.create_task(self.cleanup())

    async def run(self) -> None:
        """メインの実行ループ"""
        async with self.initialize():
            try:
                # シグナルハンドラの設定
                for sig in (signal.SIGINT, signal.SIGTERM):
                    signal.signal(sig, self.handle_signal)

                # ユーザー名の入力
                while True:

                        # SIGINTを無視する
                        signal.signal(signal.SIGINT, signal.SIG_IGN)
                        username = await asyncio.get_event_loop().run_in_executor(
                            None, lambda: input("Enter Twitch username: ")
                        )
                        # SIGINTハンドラを元に戻す
                        signal.signal(signal.SIGINT, self.handle_signal)
                        username = username.strip()
                        if username:
                            break

                # ストリーマーの存在確認
                if not await self.check_streamer_existence(username):
                    return

                # 配信状態の監視を開始
                status_task = asyncio.create_task(self.check_stream_status(username))
                self._cleanup_tasks.append(status_task)
                await status_task

            except asyncio.CancelledError:
                logger.info("Application shutdown requested")
            except Exception:
                logger.exception("Unexpected error in main loop")
            finally:
                await self.cleanup()

async def launch_terminal() -> None:
    """新しいターミナルウィンドウを非同期に開く"""
    base_path = get_base_path()
    script_path = Path(__file__).parent  / "applescript" / "launch_terminal.applescript"

    try:
        # async with aiofiles.open(script_path, mode="r") as file:
        #     script_content = (await file.read()).replace("{{base_path}}", str(base_path))

        # cmd = ["/usr/bin/osascript", "-e", str(script_content)]
        # terminal_script = f"""
        #     tell application "Terminal"
        #     activate
        #     do script "cd '{base_path}' && ./main --no-terminal"
        # end tell
        # """

        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/osascript",
            # "-e",
            # script_content,
            str(script_path),
            str(base_path),
            # terminal_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()

    except FileNotFoundError:
        logger.exception("AppleScript file not found")
    except subprocess.SubprocessError:
        logger.exception("Failed to execute AppleScript")

async def async_main() -> None:
    """非同期メイン関数"""
    if "--no-terminal" not in sys.argv and "__compiled__" in globals():
        await launch_terminal()
        return

    app = StreamNotificationApp()
    await app.run()

def main() -> None:
    """エントリーポイント"""
    try:
        asyncio.run(async_main())
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)

if __name__ == "__main__":
    main()
