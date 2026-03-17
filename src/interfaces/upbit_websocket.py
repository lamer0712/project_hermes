import json
import asyncio
import websockets
import threading
from src.utils.logger import logger


class UpbitWebSocketClient:
    """
    업비트 WebSocket API에 연결하여 타겟 코인들의 실시간 시세를 수신하는 클래스입니다.
    수신된 틱(Tick) 데이터는 등록된 콜백(RiskManager 등)으로 바로 전달됩니다.
    """

    URI = "wss://api.upbit.com/websocket/v1"

    def __init__(self, tickers: list[str], callbacks: list):
        self.tickers = tickers
        self.callbacks = callbacks
        self.running = False
        self.loop = None
        self.thread = None

    async def _connect_and_listen(self):
        while self.running:
            try:
                async with websockets.connect(
                    self.URI,
                    ping_interval=30,
                    ping_timeout=20,
                    close_timeout=5,
                    max_size=None,
                ) as websocket:

                    logger.info("[WebSocket] 연결 성공")

                    subscribe_fmt = [
                        {"ticket": "project-hermes-ws"},
                        {
                            "type": "ticker",
                            "codes": self.tickers,
                            "isOnlyRealtime": True,
                        },
                    ]

                    await websocket.send(json.dumps(subscribe_fmt))

                    while self.running:
                        try:
                            data = await asyncio.wait_for(websocket.recv(), timeout=60)
                            parsed_data = json.loads(data.decode("utf-8"))

                            ticker = parsed_data.get("code")
                            price = parsed_data.get("trade_price")

                            if ticker and price:
                                for callback in self.callbacks:
                                    asyncio.create_task(
                                        self._run_callback(callback, ticker, price)
                                    )

                        except asyncio.TimeoutError:
                            logger.warning("[WebSocket] recv timeout")
                            break

            except Exception as e:
                logger.warning(f"[WebSocket] reconnecting: {e}")
                await asyncio.sleep(3)

    def start(self):
        """웹소켓 데몬을 별도의 스레드에서 백그라운드로 실행합니다."""
        if self.running:
            return

        self.running = True
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logger.info("[WebSocket] 백그라운드 리스너 스레드 시작됨.")

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._connect_and_listen())
        except asyncio.CancelledError:
            pass
        finally:
            self.loop.close()

    async def _run_callback(self, callback, ticker, price):
        try:
            callback(ticker, price)
        except Exception as e:
            logger.error(f"[WebSocket] Callback Error: {e}")

    def stop(self):
        """웹소켓 수신을 중지합니다."""
        self.running = False
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread:
            self.thread.join(timeout=3)
        logger.info("[WebSocket] 실시간 연결이 종료되었습니다.")
