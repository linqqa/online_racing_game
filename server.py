import socket
import threading
import json
import time
import logging
import random
import math
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("server.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("server")

# Константы сервера
HOST = '127.0.0.1'
PORT = 5555
MAX_PLAYERS = 4
UPDATE_RATE = 1 / 30  # 30 раз в секунду

# Параметры игры
SCREEN_WIDTH = 810
SCREEN_HEIGHT = 810
CAR_WIDTH = 16
CAR_HEIGHT = 39

# Стартовые позиции для машин
START_POSITIONS = [
    {"x": 180, "y": 200, "angle": 0, "speed": 0},
    {"x": 210, "y": 200, "angle": 0, "speed": 0},
    {"x": 150, "y": 200, "angle": 0, "speed": 0},
    {"x": 240, "y": 200, "angle": 0, "speed": 0},
]

# Препятствия (x, y, width, height)
OBSTACLES = [
    {"x": 100, "y": 100, "width": 50, "height": 200},
    {"x": 300, "y": 300, "width": 200, "height": 50},
    {"x": 600, "y": 150, "width": 50, "height": 150},
]


class GameServer:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.server_socket = None
        self.clients = {}  # {client_id: {"socket": socket, "address": address, "nickname": nickname, "car": car_data}}
        self.player_id_counter = 0
        self.game_state = {
            "players": {},
            "obstacles": OBSTACLES,
            "game_active": False,
            "countdown": None,
            "players_ready": {},
            "race_start_time": None,
            "winner": None,
            "race_finished": False
        }
        self.chat_messages = []
        self.lock = threading.Lock()
        self.running = False

    def start(self):
        """Запуск сервера"""
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(MAX_PLAYERS)
            self.running = True
            logger.info(f"Server started on {self.host}:{self.port}")
            logger.info("Waiting for connections...")

            # Запуск обновления состояния игры
            update_thread = threading.Thread(target=self.update_game_state)
            update_thread.daemon = True
            update_thread.start()

            # Ожидание подключений
            while self.running:
                try:
                    client_socket, address = self.server_socket.accept()
                    client_thread = threading.Thread(target=self.handle_client, args=(client_socket, address))
                    client_thread.daemon = True
                    client_thread.start()
                except Exception as e:
                    logger.error(f"Error accepting connection: {e}")
        except Exception as e:
            logger.error(f"Server error: {e}")
        finally:
            self.stop()

    def stop(self):
        """Остановка сервера"""
        self.running = False
        if self.server_socket:
            self.server_socket.close()
        logger.info("Server stopped")

    def handle_client(self, client_socket, address):
        """Обработка подключения клиента"""
        player_id = None

        try:
            # Получение информации о клиенте
            init_data = self.receive_data(client_socket)
            if not init_data or "type" not in init_data or init_data["type"] != "init":
                return

            nickname = init_data["data"].get("nickname", f"Player{self.player_id_counter}")
            car_color = init_data["data"].get("car_color", "light_blue")

            with self.lock:
                player_id = self.player_id_counter
                if player_id >= MAX_PLAYERS:
                    self.send_data(client_socket, {
                        "type": "error",
                        "data": {"message": "Server is full"},
                        "timestamp": self.get_timestamp()
                    })
                    client_socket.close()
                    return

                self.player_id_counter += 1

                # Инициализация данных игрока
                car_data = START_POSITIONS[player_id].copy()
                car_data["color"] = car_color
                car_data["start_x"] = car_data["x"]  # Добавьте эту строку
                car_data["start_y"] = car_data["y"]  # Добавьте эту строку

                self.clients[player_id] = {
                    "socket": client_socket,
                    "address": address,
                    "nickname": nickname,
                    "car": car_data,
                    "last_update": time.time()
                }

                self.game_state["players"][player_id] = {
                    "nickname": nickname,
                    "car": car_data
                }

            logger.info(f"New connection from {address}, assigned player ID: {player_id}")

            # Отправка подтверждения подключения
            self.send_data(client_socket, {
                "type": "init_confirm",
                "data": {
                    "player_id": player_id,
                    "game_state": self.game_state
                },
                "timestamp": self.get_timestamp()
            })

            # Отправка уведомления всем о новом игроке
            self.broadcast_chat_message(f"Игрок {nickname} присоединился к игре", "system")

            # Основной цикл обработки сообщений от клиента
            while self.running:
                data = self.receive_data(client_socket)
                if not data:
                    break

                with self.lock:
                    self.clients[player_id]["last_update"] = time.time()

                if data["type"] == "control":
                    self.handle_control(player_id, data["data"])
                elif data["type"] == "chat":
                    self.handle_chat(player_id, data["data"])
                elif data["type"] == "ready":
                    self.handle_ready_status(player_id, data["data"])


        except Exception as e:
            logger.error(f"Error handling client {address}: {e}")
        finally:
            self.disconnect_client(player_id)

    def disconnect_client(self, player_id):
        """Отключение клиента"""
        if player_id is None or player_id not in self.clients:
            return

        with self.lock:
            try:
                nickname = self.clients[player_id]["nickname"]
                self.clients[player_id]["socket"].close()
                del self.clients[player_id]

                if player_id in self.game_state["players"]:
                    del self.game_state["players"][player_id]

                logger.info(f"Player {player_id} disconnected")

                # Уведомление всех игроков об отключении
                self.broadcast_chat_message(f"Игрок {nickname} покинул игру", "system")
            except Exception as e:
                logger.error(f"Error disconnecting client {player_id}: {e}")

    def broadcast_state(self):
        """Отправка актуального состояния игры всем клиентам"""
        game_state_update = {
            "type": "state",
            "data": self.game_state,
            "timestamp": self.get_timestamp()
        }
        for player_id, client_data in self.clients.items():
            try:
                self.send_data(client_data["socket"], game_state_update)
            except Exception as e:
                logger.error(f"Error sending state update to player {player_id}: {e}")
    def handle_control(self, player_id, control_data):
        """Обработка данных управления от клиента"""
        if player_id not in self.clients:
            return

        # Обновление данных машины игрока
        with self.lock:
            car = self.clients[player_id]["car"]

            if control_data.get("finish_collision", False):
                finish_y = control_data.get("finish_y", 0)

                if finish_y == 0:
                    # Отскок от боковой стороны финишной линии
                    car["speed"] = -car["speed"] * 0.5
                else:
                    # Проверяем, не завершена ли уже гонка
                    if not self.game_state.get("race_finished", False):
                        # Вычисляем время прохождения
                        race_time = time.time() - self.game_state.get("race_start_time", time.time())
                        race_time_formatted = f"{race_time:.2f}"

                        # Записываем информацию о победителе
                        nickname = self.clients[player_id]["nickname"]
                        self.game_state["winner"] = {
                            "player_id": player_id,
                            "nickname": nickname,
                            "time": race_time_formatted
                        }
                        self.game_state["race_finished"] = True
                        self.broadcast_state()
                        # Логируем информацию о победителе
                        logger.info(f"Player {nickname} won the race in {race_time_formatted} seconds!")
                        # Отправляем сообщение в чат
                        self.broadcast_chat_message(f"{nickname} выиграл гонку за {race_time_formatted} секунд!",
                                                    "system")

                    # Сбрасываем позицию машины
                    car["x"], car["y"] = car["start_x"], car["start_y"]
                    car["angle"] = 0
                    car["speed"] = 0

            # In server.py, modify the handle_control method around line 315-325
            if control_data.get("collision", False):
                logger.info(f"Processing collision for player {player_id}")

                # Сохраняем знак скорости для определения направления движения
                speed_sign = 1 if car["speed"] >= 0 else -1

                # Инвертируем скорость с уменьшением для предотвращения застревания
                car["speed"] = -car["speed"] * 0.3

                # Рассчитываем направление отскока с учетом направления движения
                rads = car["angle"] * (3.14159 / 180.0)

                # Если машина двигалась назад, инвертируем направление отскока
                dx = math.sin(rads) * speed_sign
                dy = math.cos(rads) * speed_sign

                # Отодвигаем машину от точки столкновения
                bounce_distance = 10.0
                car["x"] += dx * bounce_distance
                car["y"] += dy * bounce_distance

                # Добавляем небольшое случайное изменение угла для предотвращения застревания в углах
                car["angle"] += random.uniform(-5, 5)
            else:
                # Обычная логика управления
                if "up" in control_data and control_data["up"]:
                    car["speed"] = min(car["speed"] + 0.2, 5)
                elif "down" in control_data and control_data["down"]:
                    car["speed"] = max(car["speed"] - 0.2, -3)
                else:
                    # Постепенное замедление
                    if car["speed"] > 0:
                        car["speed"] = max(car["speed"] - 0.1, 0)
                    elif car["speed"] < 0:
                        car["speed"] = min(car["speed"] + 0.1, 0)

                if "right" in control_data and control_data["right"] and car["speed"] != 0:
                    car["angle"] = (car["angle"] - 5) % 360
                if "left" in control_data and control_data["left"] and car["speed"] != 0:
                    car["angle"] = (car["angle"] + 5) % 360

                # Обновление позиции
                rads = car["angle"] * (3.14159 / 180.0)
                dx = car["speed"] * -1 * math.sin(rads)
                dy = car["speed"] * -1 * math.cos(rads)

                new_x = car["x"] + dx
                new_y = car["y"] + dy

                # Проверяем границы экрана (это отдельно от коллизий с трассой)
                car_width = 16
                car_height = 39
                max_diagonal = math.sqrt(car_width ** 2 + car_height ** 2)
                padding = max_diagonal / 2

                if new_x < padding:
                    new_x = padding
                    car["speed"] = -car["speed"] * 0.5
                elif new_x > SCREEN_WIDTH - padding:
                    new_x = SCREEN_WIDTH - padding
                    car["speed"] = -car["speed"] * 0.5

                if new_y < padding:
                    new_y = padding
                    car["speed"] = -car["speed"] * 0.5
                elif new_y > SCREEN_HEIGHT - padding:
                    new_y = SCREEN_HEIGHT - padding
                    car["speed"] = -car["speed"] * 0.5

                car["x"], car["y"] = new_x, new_y

            # Обновление данных игрока в game_state
            self.game_state["players"][player_id]["car"] = car.copy()

    def handle_ready_status(self, player_id, ready_data):
        """Обработка статуса готовности от клиента"""
        if player_id not in self.clients:
            return

        is_ready = ready_data.get("ready", False)

        with self.lock:
            # Сохраняем статус как строковый ключ
            self.game_state["players_ready"][str(player_id)] = is_ready
            # Обновить время последнего взаимодействия, чтобы предотвратить таймаут
            self.clients[player_id]["last_update"] = time.time()
            logger.info(f"Player {player_id} ready status: {is_ready}")

            # Проверяем, все ли игроки готовы
            self.check_all_players_ready()

    def check_all_players_ready(self):
        """Проверка готовности всех игроков"""
        # Проверяем, есть ли хотя бы 1 игрок
        if not self.clients:
            return

        # Проверяем, все ли подключенные игроки отметились как готовые
        all_ready = True
        for player_id in self.clients.keys():
            # Проверяем наличие строкового ключа в словаре
            if str(player_id) not in self.game_state["players_ready"] or not self.game_state["players_ready"][
                str(player_id)]:
                all_ready = False
                break

        # Если все готовы и игра еще не активна, запускаем обратный отсчет
        if all_ready and not self.game_state["game_active"] and self.game_state["countdown"] is None:
            logger.info("All players ready, starting countdown")
            self.start_countdown()

    def start_countdown(self):
        """Запуск обратного отсчета перед началом игры"""
        self.game_state["countdown"] = 3  # Начинаем с 3 секунд

        # Запускаем таймер на отсчет
        countdown_thread = threading.Thread(target=self.countdown_timer)
        countdown_thread.daemon = True
        countdown_thread.start()

    def countdown_timer(self):
        """Обратный отсчет перед началом игры"""
        while self.game_state["countdown"] > 0:
            # Отправляем всем клиентам текущее значение таймера
            logger.info(f"Countdown: {self.game_state['countdown']}")
            time.sleep(1)

            with self.lock:
                self.game_state["countdown"] -= 1

        # Когда таймер дойдет до нуля, активируем игру
        with self.lock:
            self.game_state["game_active"] = True
            self.game_state["countdown"] = None
            self.game_state["race_start_time"] = time.time()  # Записываем время начала гонки
            logger.info("Game started!")

    def handle_chat(self, player_id, chat_data):
        """Обработка сообщений чата"""
        if player_id not in self.clients or "message" not in chat_data:
            return

        nickname = self.clients[player_id]["nickname"]
        message = chat_data["message"].strip()

        if not message:
            return

        self.broadcast_chat_message(message, nickname)

    def broadcast_chat_message(self, message, sender):
        """Отправка сообщения чата всем клиентам"""
        chat_message = {
            "sender": sender,
            "message": message,
            "time": self.get_timestamp()
        }

        with self.lock:
            # Добавление сообщения в историю (храним последние 20 сообщений)
            self.chat_messages.append(chat_message)
            if len(self.chat_messages) > 20:
                self.chat_messages.pop(0)

            # Отправка всем клиентам
            for client_id, client_data in self.clients.items():
                try:
                    self.send_data(client_data["socket"], {
                        "type": "chat",
                        "data": chat_message,
                        "timestamp": self.get_timestamp()
                    })
                except Exception as e:
                    logger.error(f"Error sending chat message to player {client_id}: {e}")

    def update_game_state(self):
        """Периодическое обновление состояния игры и проверка тайм-аутов"""
        while self.running:
            start_time = time.time()

            # Проверка тайм-аутов клиентов
            with self.lock:
                current_time = time.time()
                timeout_clients = []

                for player_id, client_data in self.clients.items():
                    if current_time - client_data["last_update"] > 10:  # 10 секунд тайм-аут
                        timeout_clients.append(player_id)

                # Отключение клиентов с тайм-аутом
                for player_id in timeout_clients:
                    logger.info(f"Player {player_id} timed out")
                    self.disconnect_client(player_id)

                # Отправка обновленного состояния всем клиентам
                if self.clients:
                    game_state_update = {
                        "type": "state",
                        "data": self.game_state,
                        "timestamp": self.get_timestamp()
                    }

                    for player_id, client_data in self.clients.items():
                        try:
                            self.send_data(client_data["socket"], game_state_update)
                        except Exception as e:
                            logger.error(f"Error sending state update to player {player_id}: {e}")

            # Поддержание частоты обновления
            elapsed = time.time() - start_time
            sleep_time = max(0, UPDATE_RATE - elapsed)
            time.sleep(sleep_time)

    def send_data(self, client_socket, data):
        """Отправка данных клиенту"""
        try:
            message = json.dumps(data).encode('utf-8')
            message_len = len(message).to_bytes(4, byteorder='big')
            client_socket.send(message_len + message)
        except Exception as e:
            raise Exception(f"Error sending data: {e}")

    def receive_data(self, client_socket):
        """Получение данных от клиента"""
        try:
            # Получение длины сообщения
            message_len_bytes = client_socket.recv(4)
            if not message_len_bytes:
                return None

            message_len = int.from_bytes(message_len_bytes, byteorder='big')

            # Получение самого сообщения
            chunks = []
            bytes_received = 0
            while bytes_received < message_len:
                chunk = client_socket.recv(min(message_len - bytes_received, 4096))
                if not chunk:
                    return None
                chunks.append(chunk)
                bytes_received += len(chunk)

            message = b''.join(chunks).decode('utf-8')
            return json.loads(message)
        except Exception as e:
            logger.error(f"Error receiving data: {e}")
            return None

    def get_timestamp(self):
        """Получение текущей метки времени"""
        return datetime.now().isoformat()


if __name__ == "__main__":
    import math  # Импорт math для тригонометрических вычислений в handle_control

    server = GameServer(HOST, PORT)
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Server stopped by keyboard interrupt")
        server.stop()