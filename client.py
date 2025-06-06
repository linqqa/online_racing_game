import pygame
import pygame_menu
import socket
import json
import threading
import time
import math
import logging
from datetime import datetime

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("client.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("client")

# Константы игры
SCREEN_WIDTH = 810
SCREEN_HEIGHT = 810
FPS = 30

# Настройки сервера
SERVER_HOST = '127.0.0.1'
SERVER_PORT = 5555

# Цвета
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
YELLOW = (255, 255, 0)
GRAY = (128, 128, 128)

# Доступные цвета машин
CAR_COLORS = [
    ("Голубая", "light_blue"),
    ("Розовая", "pink"),
    ("Синяя", "blue"),
    ("Зеленая", "green")
]


class GameClient:
    def __init__(self):
        # Инициализация Pygame
        pygame.init()
        pygame.font.init()

        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        pygame.display.set_caption("Гонки")
        self.clock = pygame.time.Clock()

        # Шрифты
        self.font = pygame.font.SysFont(None, 24)
        self.font_large = pygame.font.SysFont(None, 36)

        # Игровые переменные
        self.player_id = None
        self.nickname = "Player"
        self.car_color = "light_blue"
        self.game_state = {"players": {}, "obstacles": []}
        self.connected = False
        self.socket = None
        self.chat_messages = []
        self.chat_active = False
        self.chat_input = ""
        self.reconnect_attempts = 0
        self.player_ready = False
        self.ready_button = None
        self.chat_scroll_position = 0

        # Загрузка изображений
        self.car_images = self.load_car_images()
        self.grass_img = pygame.transform.scale(pygame.image.load("assets/grass.png"), (SCREEN_WIDTH, SCREEN_HEIGHT))
        self.track_img = pygame.transform.scale(pygame.image.load("assets/track.png"), (SCREEN_WIDTH, SCREEN_HEIGHT))
        self.track_border_img = pygame.transform.scale(pygame.image.load("assets/track-border.png"),
                                                       (SCREEN_WIDTH, SCREEN_HEIGHT))
        self.finish_img = pygame.image.load("assets/finish.png")
        self.track_border_mask = pygame.mask.from_surface(self.track_border_img)
        self.finish_mask = pygame.mask.from_surface(self.finish_img)

        self.finish_position = (130, 250)

        # Сетевые потоки
        self.receiver_thread = None
        self.running = True

        # Создание меню
        self.menu = self.create_menu()

        # Блокировка для потоков
        self.lock = threading.Lock()

    def load_car_images(self):
        """Загрузка изображений машин из папки assets"""
        images = {}
        # Соответствие цветов и изображений
        car_mapping = {
            "light_blue": ["car1.png", (16, 39)],
            "pink": ["car2.png", (16, 39)],
            "blue": ["car3.png", (16, 39)],
            "green": ["car4.png", (16, 39)]
        }

        # Загружаем и масштабируем изображения машин
        for color_name, car_info in car_mapping.items():
            car_file, car_size = car_info
            car_img = pygame.image.load(f"assets/{car_file}")
            car_img = pygame.transform.scale(car_img, car_size)
            images[color_name] = car_img

        return images

    def check_collision(self, car_data):
        """Проверка коллизий машины с границами трассы"""
        if not car_data:
            return False

        # Получаем изображение машины
        car_img = self.car_images.get(car_data["color"], self.car_images["light_blue"])

        # Поворачиваем изображение согласно углу машины
        rotated_car = pygame.transform.rotate(car_img, car_data["angle"])
        car_mask = pygame.mask.from_surface(rotated_car)

        # Определяем центр машины для правильного позиционирования
        car_rect = rotated_car.get_rect(center=(car_data["x"], car_data["y"]))

        # Правильное смещение для проверки коллизии (используем topleft для mask.overlap)
        offset = (car_rect.topleft[0], car_rect.topleft[1])

        # Проверяем коллизию с границей трассы
        collision_point = self.track_border_mask.overlap(car_mask, offset)

        if collision_point is not None:
            logger.info(f"Collision detected at {collision_point}")
        return collision_point is not None

    def create_menu(self):
        """Создание главного меню игры"""
        self.menu = pygame_menu.Menu(
            "Гоночная игра",
            SCREEN_WIDTH,
            SCREEN_HEIGHT,
            theme=pygame_menu.themes.THEME_DARK
        )

        # Поле для ввода никнейма
        self.menu.add.text_input('Никнейм: ', default=self.nickname, onchange=self.set_nickname)

        # Выбор цвета машины
        self.menu.add.selector(
            "Цвет машины: ",
            [(color[0], color[1]) for color in CAR_COLORS],
            onchange=self.set_car_color
        )

        # Кнопки
        self.menu.add.button('Подключиться', self.connect_to_server)
        self.menu.add.button('Выход', pygame_menu.events.EXIT)

        return self.menu

    def render_victory_screen(self):
        """Отрисовка экрана завершения гонки"""
        # Получаем данные о победителе
        winner_data = self.game_state.get("winner")
        if not winner_data:
            return False

        # Создаем полупрозрачный фон
        victory_surface = pygame.Surface((600, 300), pygame.SRCALPHA)
        victory_surface.fill((0, 0, 0, 200))
        pygame.draw.rect(victory_surface, (50, 200, 50), (0, 0, 600, 300), 3)

        # Заголовок
        title_text = self.font_large.render("ПОБЕДИТЕЛЬ!", True, (255, 255, 0))
        victory_surface.blit(title_text, (300 - title_text.get_width() // 2, 30))

        # Имя победителя
        winner_text = self.font_large.render(winner_data["nickname"], True, WHITE)
        victory_surface.blit(winner_text, (300 - winner_text.get_width() // 2, 80))

        # Время прохождения
        time_text = self.font.render(f"Время: {winner_data['time']} сек", True, WHITE)
        victory_surface.blit(time_text, (300 - time_text.get_width() // 2, 130))

        # Кнопка возврата в меню
        menu_button = pygame.Rect(200, 180, 200, 50)
        pygame.draw.rect(victory_surface, GREEN, menu_button)
        pygame.draw.rect(victory_surface, WHITE, menu_button, 2)

        button_text = self.font.render("ОК", True, WHITE)
        victory_surface.blit(button_text, (300 - button_text.get_width() // 2, 195))

        # Отображаем экран победителя
        self.screen.blit(victory_surface, (SCREEN_WIDTH // 2 - 300, SCREEN_HEIGHT // 2 - 150))

        # Сохраняем прямоугольник кнопки для обработки кликов
        self.menu_button = pygame.Rect(
            SCREEN_WIDTH // 2 - 100,
            SCREEN_HEIGHT // 2 + 30,
            200, 50
        )

        return True

    def set_nickname(self, value):
        """Установка никнейма игрока"""
        self.nickname = value

    def set_car_color(self, selected_value, color):
        """Установка цвета машины"""
        self.car_color = color
        logger.info(f"Выбран цвет машины: {color}")

    def connect_to_server(self):
        """Подключение к серверу"""
        try:
            if self.socket:
                self.socket.close()

            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((SERVER_HOST, SERVER_PORT))

            # Отправка начальных данных
            self.send_data({
                "type": "init",
                "data": {
                    "nickname": self.nickname,
                    "car_color": self.car_color
                },
                "timestamp": self.get_timestamp()
            })

            # Получение подтверждения
            init_data = self.receive_data()
            if init_data and init_data["type"] == "init_confirm":
                self.player_id = init_data["data"]["player_id"]
                self.game_state = init_data["data"]["game_state"]
                self.connected = True
                self.reconnect_attempts = 0
                logger.info(f"Connected to server as player {self.player_id}")

                # Запуск потока для получения данных
                self.receiver_thread = threading.Thread(target=self.receive_updates)
                self.receiver_thread.daemon = True
                self.receiver_thread.start()

                # Выход из меню и начало игры
                return True
            else:
                logger.error("Failed to connect: did not receive proper initialization")
                self.show_error("Ошибка соединения", "Не удалось подключиться к серверу")
                return False

        except Exception as e:
            logger.error(f"Connection error: {e}")
            self.show_error("Ошибка соединения", f"Не удалось подключиться к серверу: {e}")
            return False

    def reconnect(self):
        """Попытка переподключения к серверу"""
        if self.reconnect_attempts >= 5:
            logger.error("Max reconnect attempts reached")
            self.show_error("Ошибка соединения", "Превышено количество попыток переподключения")
            return False

        logger.info(f"Attempting to reconnect ({self.reconnect_attempts + 1}/5)")
        self.reconnect_attempts += 1

        try:
            if self.socket:
                try:
                    self.socket.close()
                except:
                    pass
                self.socket = None

            # Создаем новый сокет
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(5)  # Добавляем таймаут для подключения
            self.socket.connect((SERVER_HOST, SERVER_PORT))
            self.socket.settimeout(None)  # Сбрасываем таймаут после подключения

            # Отправка начальных данных
            self.send_data({
                "type": "init",
                "data": {
                    "nickname": self.nickname,
                    "car_color": self.car_color
                },
                "timestamp": self.get_timestamp()
            })

            # Получение подтверждения
            init_data = self.receive_data()
            if init_data and init_data["type"] == "init_confirm":
                self.player_id = init_data["data"]["player_id"]
                self.game_state = init_data["data"]["game_state"]
                self.connected = True
                self.reconnect_attempts = 0
                logger.info(f"Reconnected to server as player {self.player_id}")

                # Запуск потока для получения данных
                self.receiver_thread = threading.Thread(target=self.receive_updates)
                self.receiver_thread.daemon = True
                self.receiver_thread.start()

                return True
            else:
                logger.error("Failed to reconnect: did not receive proper initialization")
                return False
        except Exception as e:
            logger.error(f"Reconnection error: {e}")
            return False

    def disconnect(self):
        """Отключение от сервера"""
        self.connected = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        logger.info("Disconnected from server")

    def send_data(self, data):
        """Отправка данных на сервер"""
        try:
            message = json.dumps(data).encode('utf-8')
            message_len = len(message).to_bytes(4, byteorder='big')
            self.socket.send(message_len + message)
        except Exception as e:
            logger.error(f"Error sending data: {e}")
            self.connected = False
            raise e

    def receive_data(self):
        """Получение данных от сервера"""
        try:
            # Получение длины сообщения
            message_len_bytes = self.socket.recv(4)
            if not message_len_bytes:
                return None

            message_len = int.from_bytes(message_len_bytes, byteorder='big')

            # Получение самого сообщения
            chunks = []
            bytes_received = 0
            while bytes_received < message_len:
                chunk = self.socket.recv(min(message_len - bytes_received, 4096))
                if not chunk:
                    return None
                chunks.append(chunk)
                bytes_received += len(chunk)

            message = b''.join(chunks).decode('utf-8')
            return json.loads(message)
        except Exception as e:
            logger.error(f"Error receiving data: {e}")
            self.connected = False
            return None

    def receive_updates(self):
        """Поток получения обновлений от сервера"""
        while self.running and self.connected:
            try:
                data = self.receive_data()
                if not data:
                    logger.warning("Lost connection to server")
                    self.connected = False
                    break

                if data["type"] == "state":
                    logger.info(f"Получено состояние: {data['data']}")
                    with self.lock:
                        self.game_state = data["data"]
                elif data["type"] == "chat":
                    with self.lock:
                        self.chat_messages.append(data["data"])
                        # Ограничиваем количество сообщений
                        if len(self.chat_messages) > 10:
                            self.chat_messages.pop(0)
            except Exception as e:
                logger.error(f"Error in receive updates: {e}")
                self.connected = False
                break

    def send_control_input(self, keys):
        """Отправка данных управления на сервер"""
        if not self.connected:
            return

        # Добавляем проверку - если игра не активна, не отправляем управление
        if not self.game_state.get("game_active", False):
            return

        control_data = {
            "up": keys[pygame.K_w] or keys[pygame.K_UP],
            "down": keys[pygame.K_s] or keys[pygame.K_DOWN],
            "left": keys[pygame.K_a] or keys[pygame.K_LEFT],
            "right": keys[pygame.K_d] or keys[pygame.K_RIGHT],
            "collision": False  # Добавляем флаг коллизии
        }

        # Получаем данные о машине текущего игрока
        my_car = None
        if str(self.player_id) in self.game_state.get("players", {}):
            my_car = self.game_state["players"][str(self.player_id)]["car"]
        elif self.player_id in self.game_state.get("players", {}):
            my_car = self.game_state["players"][self.player_id]["car"]

        # Проверяем коллизии с границами трассы
        if my_car:
            collision = self.check_collision(my_car)
            if collision:
                control_data["collision"] = True

        if my_car:
            # Проверка коллизий с финишной линией
            car_img = self.car_images.get(my_car["color"], self.car_images["light_blue"])
            rotated_car = pygame.transform.rotate(car_img, my_car["angle"])
            car_mask = pygame.mask.from_surface(rotated_car)
            car_rect = rotated_car.get_rect(center=(my_car["x"], my_car["y"]))

            finish_offset = (car_rect.topleft[0] - self.finish_position[0],
                             car_rect.topleft[1] - self.finish_position[1])
            finish_collision = self.finish_mask.overlap(car_mask, finish_offset)

            if finish_collision:
                # Определяем, с какой стороны произошла коллизия
                finish_y = finish_collision[1]
                control_data["finish_collision"] = True
                control_data["finish_y"] = finish_y

        try:
            self.send_data({
                "type": "control",
                "data": control_data,
                "timestamp": self.get_timestamp()
            })
        except Exception as e:
            logger.error(f"Error sending control data: {e}")

    def send_chat_message(self, message):
        """Отправка сообщения чата"""
        if not self.connected or not message.strip():
            return

        try:
            self.send_data({
                "type": "chat",
                "data": {
                    "message": message
                },
                "timestamp": self.get_timestamp()
            })
            self.chat_input = ""

            # После отправки сообщения автоматически прокручиваем чат вниз
            with self.lock:
                max_messages = len(self.chat_messages)
                visible_messages = 5
                max_scroll = max(0, max_messages - visible_messages)
                self.chat_scroll_position = max_scroll

        except Exception as e:
            logger.error(f"Error sending chat message: {e}")

    def handle_chat_input(self, event):
        """Обработка ввода в чате"""
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                self.send_chat_message(self.chat_input)
                self.chat_active = False
            elif event.key == pygame.K_ESCAPE:
                self.chat_input = ""
                self.chat_active = False
            elif event.key == pygame.K_BACKSPACE:
                self.chat_input = self.chat_input[:-1]
            else:
                if len(self.chat_input) < 50:  # Ограничение длины сообщения
                    self.chat_input += event.unicode

    # Добавляем метод для отправки статуса готовности
    def send_ready_status(self, is_ready):
        """Отправка статуса готовности на сервер"""
        if not self.connected:
            return

        try:
            logger.info(f"Sending ready status: {is_ready}")
            self.send_data({
                "type": "ready",
                "data": {
                    "ready": is_ready
                },
                "timestamp": self.get_timestamp()
            })
        except Exception as e:
            logger.error(f"Error sending ready status: {e}")

    def render_chat(self):
        """Отображение чата с возможностью прокрутки"""
        # Фон чата, увеличиваем ширину для ползунка
        chat_width = 430
        chat_height = 150
        chat_surface = pygame.Surface((chat_width, chat_height), pygame.SRCALPHA)
        chat_surface.fill((0, 0, 0, 128))  # Полупрозрачный фон

        # Область сообщений
        messages_area_width = 400
        scrollbar_width = 20

        # Инициализация позиции прокрутки, если еще не задана
        if not hasattr(self, 'chat_scroll_position'):
            self.chat_scroll_position = 0

        with self.lock:
            # Расчет максимальной позиции прокрутки
            max_messages = len(self.chat_messages)
            visible_messages = 5  # Количество видимых сообщений
            max_scroll = max(0, max_messages - visible_messages)

            # Ограничиваем позицию прокрутки
            self.chat_scroll_position = min(max_scroll, self.chat_scroll_position)

            # Отображаем сообщения начиная с позиции прокрутки
            display_messages = self.chat_messages[
                               self.chat_scroll_position:self.chat_scroll_position + visible_messages]

            # Отображение сообщений
            y_offset = 10
            for message in display_messages:
                sender = message["sender"]
                text = message["message"]

                if sender == "system":
                    # Системные сообщения
                    text_surface = self.font.render(text, True, YELLOW)
                else:
                    # Обычные сообщения
                    text_surface = self.font.render(f"{sender}: {text}", True, WHITE)

                chat_surface.blit(text_surface, (10, y_offset))
                y_offset += 25

            # Отображаем ползунок прокрутки только когда чат активен
            if self.chat_active and max_scroll > 0:
                # Фон ползунка
                scrollbar_bg_rect = pygame.Rect(messages_area_width, 0, scrollbar_width, chat_height - 40)
                pygame.draw.rect(chat_surface, (60, 60, 60), scrollbar_bg_rect)

                # Расчет размера и позиции ползунка
                scrollbar_height = max(20, (visible_messages / max_messages) * (chat_height - 40))
                scrollbar_pos = (self.chat_scroll_position / max_scroll) * (chat_height - 40 - scrollbar_height)

                # Рисуем сам ползунок
                scrollbar_rect = pygame.Rect(
                    messages_area_width + 2,
                    scrollbar_pos,
                    scrollbar_width - 4,
                    scrollbar_height
                )
                pygame.draw.rect(chat_surface, (150, 150, 150), scrollbar_rect)

                # Стрелки прокрутки вверх и вниз
                up_arrow_rect = pygame.Rect(messages_area_width, chat_height - 40, scrollbar_width, 20)
                down_arrow_rect = pygame.Rect(messages_area_width, chat_height - 20, scrollbar_width, 20)

                pygame.draw.rect(chat_surface, (80, 80, 80), up_arrow_rect)
                pygame.draw.rect(chat_surface, (80, 80, 80), down_arrow_rect)

                # Треугольники для стрелок
                # Стрелка вверх
                pygame.draw.polygon(chat_surface, WHITE, [
                    (messages_area_width + scrollbar_width // 2, chat_height - 35),
                    (messages_area_width + 5, chat_height - 25),
                    (messages_area_width + scrollbar_width - 5, chat_height - 25)
                ])

                # Стрелка вниз
                pygame.draw.polygon(chat_surface, WHITE, [
                    (messages_area_width + scrollbar_width // 2, chat_height - 5),
                    (messages_area_width + 5, chat_height - 15),
                    (messages_area_width + scrollbar_width - 5, chat_height - 15)
                ])

        # Поле ввода, если чат активен
        if self.chat_active:
            # Фон поля ввода
            pygame.draw.rect(chat_surface, (50, 50, 50), (10, 110, 380, 30))
            pygame.draw.rect(chat_surface, (100, 100, 100), (10, 110, 380, 30), 2)

            # Текст ввода
            input_text = self.font.render(self.chat_input, True, WHITE)
            chat_surface.blit(input_text, (15, 115))

            # Курсор ввода (мигающий)
            if int(time.time() * 2) % 2 == 0:
                cursor_x = 15 + input_text.get_width()
                pygame.draw.line(chat_surface, WHITE, (cursor_x, 115), (cursor_x, 135), 2)

        self.screen.blit(chat_surface, (10, SCREEN_HEIGHT - 160))

    def render_game(self):
        """Отрисовка игры"""
        # Отрисовка фона (трава)
        self.screen.blit(self.grass_img, (0, 0))

        # Отрисовка трассы
        self.screen.blit(self.track_img, (0, 0))

        # Отрисовка финишной линии
        self.screen.blit(self.finish_img, self.finish_position)

        # Отрисовка границ трассы
        self.screen.blit(self.track_border_img, (0, 0))

        # Проверяем статус игры
        if self.game_state.get("game_active", False):
            # Игра активна - обычный рендеринг
            pass
        else:
            # Игра ожидает - показываем кнопку Ready или статус ожидания
            players_ready = self.game_state.get("players_ready", {})
            player_id_str = str(self.player_id)

            server_ready_status = players_ready.get(player_id_str, False)

            # Проверяем статус в словаре, учитывая что ключи могут быть и строками, и числами
            if player_id_str in players_ready:
                server_ready_status = players_ready[player_id_str]
            elif self.player_id in players_ready:
                server_ready_status = players_ready[self.player_id]

            # Если игрок еще не готов по данным сервера, показываем кнопку
            if not server_ready_status:
                # Создаем поверхность для кнопки
                ready_button = pygame.Surface((200, 50))
                ready_button.fill(GREEN)
                ready_text = self.font.render("READY", True, WHITE)
                ready_button.blit(ready_text, (100 - ready_text.get_width() // 2, 25 - ready_text.get_height() // 2))

                # Сохраняем прямоугольник кнопки для обработки кликов
                self.ready_button = pygame.Rect(SCREEN_WIDTH // 2 - 100, 50, 200, 50)
                self.screen.blit(ready_button, (SCREEN_WIDTH // 2 - 100, 50))
            else:
                # Если игрок готов по данным сервера, показываем сообщение
                ready_text = self.font.render("Вы готовы!", True, GREEN)
                self.screen.blit(ready_text, (SCREEN_WIDTH // 2 - ready_text.get_width() // 2, 60))

            # Отображение статуса других игроков
            players = self.game_state.get("players", {})

            status_text = "Ожидание готовности игроков:"
            status_surface = self.font.render(status_text, True, WHITE)
            self.screen.blit(status_surface, (SCREEN_WIDTH // 2 - status_surface.get_width() // 2, 120))

            y_offset = 160
            for player_id, player_data in self.game_state.get("players", {}).items():
                car = player_data["car"]
                nickname = player_data["nickname"]

                # Получение правильного изображения машины с учетом новых цветов
                car_img = self.car_images.get(car["color"],
                                              self.car_images["light_blue"])  # Изменено с "red" на "light_blue"

                # Используем функцию blit_rotate_center для отрисовки машины
                blit_rotate_center(self.screen, car_img, (car["x"], car["y"]), car["angle"])

                # Отрисовка никнейма над машиной
                name_text = self.font.render(nickname, True, WHITE)
                self.screen.blit(name_text, (car["x"] + 20 - name_text.get_width() // 2, car["y"] - 20))

            # Если идет обратный отсчет, показываем его
            countdown = self.game_state.get("countdown")
            if countdown is not None:
                countdown_text = self.font_large.render(str(countdown), True, RED)
                self.screen.blit(countdown_text, (SCREEN_WIDTH // 2 - countdown_text.get_width() // 2,
                                                  SCREEN_HEIGHT // 2 - countdown_text.get_height() // 2))

        # Отрисовка игроков - ПЕРЕМЕЩЕНО за пределы условия
        for player_id, player_data in self.game_state.get("players", {}).items():
            car = player_data["car"]
            nickname = player_data["nickname"]

            # Получение правильного изображения машины
            car_img = self.car_images.get(car["color"], self.car_images["light_blue"])

            # Используем функцию blit_rotate_center для отрисовки машины
            blit_rotate_center(self.screen, car_img, (car["x"], car["y"]), car["angle"])

            # Отрисовка никнейма над машиной - ПЕРЕМЕЩЕНО внутрь цикла
            name_text = self.font.render(nickname, True, WHITE)
            self.screen.blit(name_text, (car["x"] + 20 - name_text.get_width() // 2, car["y"] - 20))

        # Отрисовка чата
        self.render_chat()

        # В методе render_game замените блок с победой на:
        if self.game_state.get("race_finished", False):
            if not getattr(self, 'victory_message_active', False):
                logger.info(f"race_finished: {self.game_state.get('race_finished')}")
                logger.info(f"winner: {self.game_state.get('winner')}")
                self.show_victory_message()
            if self.victory_message_active:
                self.screen.blit(self.victory_surface, (self.victory_x, self.victory_y))



        # Add this at the end of render_game method, before the disconnection message check
        if hasattr(self, 'victory_message_active') and self.victory_message_active:
            self.screen.blit(self.victory_surface, (self.victory_x, self.victory_y))

        # Если игрок отключен, показать сообщение
        if not self.connected:
            reconnect_surface = pygame.Surface((600, 150), pygame.SRCALPHA)
            reconnect_surface.fill((0, 0, 0, 200))
            pygame.draw.rect(reconnect_surface, (255, 50, 50), (0, 0, 600, 150), 2)

            text1 = self.font_large.render("Потеряно соединение с сервером", True, RED)
            text2 = self.font.render("Нажмите R для переподключения или ESC для выхода в меню", True, WHITE)

            reconnect_surface.blit(text1, (300 - text1.get_width() // 2, 30))
            reconnect_surface.blit(text2, (300 - text2.get_width() // 2, 70))

            self.screen.blit(reconnect_surface, (SCREEN_WIDTH // 2 - 300, SCREEN_HEIGHT // 2 - 75))

    def show_victory_message(self):
        """Show victory message in the center of the screen"""
        # Get winner data
        winner_data = self.game_state.get("winner")
        if not winner_data:
            self.victory_message_active = False
            return False

        # Create surface for victory message
        victory_surface = pygame.Surface((600, 250))
        victory_surface.fill((50, 50, 50))
        pygame.draw.rect(victory_surface, (50, 200, 50), (0, 0, 600, 250), 2)  # Add green border

        # Draw title
        title_text = self.font_large.render("ПОБЕДИТЕЛЬ!", True, (255, 255, 0))
        victory_surface.blit(title_text, (300 - title_text.get_width() // 2, 30))

        # Draw winner message
        message = f"Игрок {winner_data['nickname']} выиграл гонку за {winner_data['time']} секунд!"

        # Split message into lines if needed
        words = message.split()
        message_lines = []
        current_line = ""
        for word in words:
            if len(current_line + " " + word) <= 50:
                current_line += (" " + word if current_line else word)
            else:
                message_lines.append(current_line)
                current_line = word

        if current_line:
            message_lines.append(current_line)

        y_offset = 80
        for line in message_lines:
            message_text = self.font.render(line, True, (255, 255, 255))
            victory_surface.blit(message_text, (300 - message_text.get_width() // 2, y_offset))
            y_offset += 25

        # Draw OK button
        ok_button_rect = pygame.Rect(250, 200, 100, 30)
        pygame.draw.rect(victory_surface, (0, 255, 0), ok_button_rect)
        ok_text = self.font.render("OK", True, (255, 255, 255))
        victory_surface.blit(ok_text, (300 - ok_text.get_width() // 2, 215 - ok_text.get_height() // 2))

        # Save button rect for click detection in main loop
        self.victory_message_active = True
        self.victory_x = SCREEN_WIDTH // 2 - 300
        self.victory_y = 100
        self.victory_surface = victory_surface
        self.victory_ok_button = pygame.Rect(
            self.victory_x + ok_button_rect.x,
            self.victory_y + ok_button_rect.y,
            ok_button_rect.width,
            ok_button_rect.height
        )

        return True

    def show_error(self, title, message):
        """Показать сообщение об ошибке с автоматическим закрытием через 5 секунд"""
        # Создаем поверхность большего размера для сообщения об ошибке
        error_surface = pygame.Surface((600, 250))
        error_surface.fill((50, 50, 50))
        pygame.draw.rect(error_surface, (100, 100, 100), (0, 0, 600, 250), 2)  # Добавляем рамку

        # Рисуем заголовок
        title_text = self.font_large.render(title, True, RED)
        error_surface.blit(title_text, (300 - title_text.get_width() // 2, 30))

        # Рисуем сообщение (может быть многострочным)
        message_lines = message.split('\n')
        if len(message_lines) == 1 and len(message) > 50:
            # Автоматически разбиваем длинные сообщения на строки
            words = message.split()
            message_lines = []
            current_line = ""

            for word in words:
                if len(current_line + " " + word) <= 50:
                    current_line += (" " + word if current_line else word)
                else:
                    message_lines.append(current_line)
                    current_line = word

            if current_line:
                message_lines.append(current_line)

        y_offset = 80
        for line in message_lines:
            message_text = self.font.render(line, True, WHITE)
            error_surface.blit(message_text, (300 - message_text.get_width() // 2, y_offset))
            y_offset += 25

        # Рисуем кнопку OK
        ok_button_rect = pygame.Rect(250, 200, 100, 30)
        pygame.draw.rect(error_surface, GREEN, ok_button_rect)
        ok_text = self.font.render("OK", True, WHITE)
        error_surface.blit(ok_text, (300 - ok_text.get_width() // 2, 215 - ok_text.get_height() // 2))

        # Центрируем окно ошибки на экране
        error_x = SCREEN_WIDTH // 2 - 300
        error_y = 100
        # error_y = SCREEN_HEIGHT // 2 - 125  # Центрируем по вертикали

        # Задаем время автоматического закрытия окна (5 секунд)
        auto_close_time = time.time() + 5

        # Начинаем цикл ожидания
        waiting_for_close = True
        while waiting_for_close:
            # Проверяем, не пора ли автоматически закрыть окно
            if time.time() >= auto_close_time:
                waiting_for_close = False

            # Обрабатываем события
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    waiting_for_close = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    mouse_pos = pygame.mouse.get_pos()
                    # Используем позицию, куда реально отрисовываем окно
                    screen_button_rect = pygame.Rect(
                        error_x + ok_button_rect.x,
                        error_y + ok_button_rect.y,
                        ok_button_rect.width,
                        ok_button_rect.height
                    )
                    if screen_button_rect.collidepoint(mouse_pos):
                        waiting_for_close = False

            # Очищаем экран для рисования меню
            self.screen.fill((0, 0, 0))

            # Рисуем основное меню
            if self.menu.is_enabled():
                self.menu.draw(self.screen)

            # # Добавляем таймер обратного отсчета в окно ошибки
            # seconds_left = int(auto_close_time - time.time())
            # if seconds_left > 0:
            #     timer_text = self.font.render(f"Автозакрытие через {seconds_left} сек",True,YELLOW)
            #     error_surface.blit(timer_text,(300 - timer_text.get_width() // 2,235))

            # Рисуем окно ошибки поверх меню
            self.screen.blit(error_surface, (error_x, error_y))
            pygame.display.flip()
            self.clock.tick(30)

        # Очищаем сокет при возврате в меню
        if self.socket:
            try:
                self.socket.close()
                self.socket = None
            except:
                pass

        # Сбрасываем любые попытки переподключения
        self.reconnect_attempts = 0

        # Убеждаемся, что основное меню активно
        self.player_id = None

    def return_to_main_menu(self):
        """Сбрасывает состояние игры и возвращает к главному меню"""
        logger.info("Returning to main menu")

        # Сбрасываем id игрока, чтобы вызвать отображение главного меню
        self.player_id = None

        # Отключаемся от сервера, если соединение активно
        if self.connected:
            self.disconnect()

        # Очищаем игровое состояние
        self.game_state = {"players": {}, "obstacles": []}
        self.chat_messages = []
        self.chat_active = False
        self.player_ready = False
        self.ready_button = None

        # Возвращаемся к обычному циклу, который отобразит главное меню
        return

    def get_timestamp(self):
        """Получение текущей метки времени"""
        return datetime.now().isoformat()

    def run(self):
        """Основной игровой цикл"""
        while self.running:
            # Обработка событий
            events = pygame.event.get()
            for event in events:
                if event.type == pygame.QUIT:
                    self.running = False

                # Если в игре и чат активен
                if self.connected and self.chat_active:
                    self.handle_chat_input(event)
                    # Добавляем обработку событий мыши для прокрутки чата
                    if event.type == pygame.MOUSEBUTTONDOWN:
                        if event.button in (4, 5):  # Колесо мыши вверх/вниз
                            # Прокрутка колесом мыши
                            if event.button == 4:  # Прокрутка вверх
                                self.chat_scroll_position = max(0, self.chat_scroll_position - 1)
                            elif event.button == 5:  # Прокрутка вниз
                                max_messages = len(self.chat_messages)
                                visible_messages = 5
                                max_scroll = max(0, max_messages - visible_messages)
                                self.chat_scroll_position = min(max_scroll, self.chat_scroll_position + 1)

                        elif event.button == 1:  # Левая кнопка мыши
                            mouse_pos = pygame.mouse.get_pos()
                            chat_x, chat_y = 10, SCREEN_HEIGHT - 160
                            chat_height = 150

                            # Проверяем, нажата ли кнопка вверх
                            up_arrow_rect = pygame.Rect(
                                chat_x + 400,
                                chat_y + chat_height - 40,
                                20,
                                20
                            )
                            if up_arrow_rect.collidepoint(mouse_pos):
                                self.chat_scroll_position = max(0, self.chat_scroll_position - 1)

                            # Проверяем, нажата ли кнопка вниз
                            down_arrow_rect = pygame.Rect(
                                chat_x + 400,
                                chat_y + chat_height - 20,
                                20,
                                20
                            )
                            if down_arrow_rect.collidepoint(mouse_pos):
                                max_messages = len(self.chat_messages)
                                visible_messages = 5
                                max_scroll = max(0, max_messages - visible_messages)
                                self.chat_scroll_position = min(max_scroll, self.chat_scroll_position + 1)

                    continue

                # Добавляем обработку кликов для кнопки Ready
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if self.game_state and not self.game_state.get("game_active", False):
                        if self.ready_button and self.ready_button.collidepoint(event.pos):
                            self.send_ready_status(True)

                    # Проверяем нажатие на кнопку "ОК" на экране победителя
                    if self.game_state.get("race_finished", False) and hasattr(self, 'menu_button'):
                        if self.menu_button.collidepoint(event.pos):
                            self.return_to_main_menu()

                    # Check for clicks on the victory message OK button
                    if getattr(self, 'victory_message_active', False):
                        if hasattr(self, 'victory_ok_button') and self.victory_ok_button.collidepoint(event.pos):
                            self.victory_message_active = False
                            self.return_to_main_menu()

                # Если в игре
                if self.connected:
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_t:
                            # Активация чата
                            self.chat_active = True
                        elif event.key == pygame.K_ESCAPE:
                            # Выход в меню
                            self.disconnect()
                # Если отключен
                elif not self.connected and self.player_id is not None:
                    if event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_r:
                            # Попытка переподключения
                            self.reconnect()
                        elif event.key == pygame.K_ESCAPE:
                            # Сброс данных и возврат в меню
                            self.player_id = None

            # Обновление меню или игры
            if self.player_id is None:
                # В меню
                self.menu.update(events)
                if self.menu.is_enabled():
                    self.menu.draw(self.screen)
            else:
                # В игре
                if self.connected and not self.chat_active:
                    # Отправка управления, только если подключен и не в чате
                    keys = pygame.key.get_pressed()
                    self.send_control_input(keys)

                # Отрисовка игры
                self.render_game()

            # Обновление экрана
            pygame.display.flip()
            self.clock.tick(FPS)

        # Очистка при выходе
        self.disconnect()
        pygame.quit()


def blit_rotate_center(win, image, top_left, angle):
    """Функция для отрисовки повернутого изображения с центром в указанной точке"""
    rotated_image = pygame.transform.rotate(image, angle)
    new_rect = rotated_image.get_rect(center=image.get_rect(center=top_left).center)
    win.blit(rotated_image, new_rect.topleft)


if __name__ == "__main__":
    client = GameClient()
    client.run()
