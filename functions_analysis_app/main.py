import sys
import threading
import queue
import asyncio
import os
import tkinter as tk

# Убедимся, что Python может найти наши модули
# Это важно для запуска из корневой папки проекта или в тестовой среде
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


from backend.pipeline import FullAnalysisPipeline
from frontend.gui_app import GUIApp
from backend.utils import set_ui_queue_callback
import config  # Импортируем наш файл конфигурации


def main():
    # Создаем очереди для обмена сообщениями между UI и бэкендом
    ui_queue = queue.Queue()  # Сообщения от бэкенда к UI

    # Устанавливаем UI очередь в utils, чтобы все логи, статусы и прогресс
    # автоматически отправлялись в эту очередь, а затем обрабатывались UI.
    set_ui_queue_callback(ui_queue.put)

    # Инициализируем бэкенд (основную логику)
    # Передаем ui_queue, чтобы бэкенд мог отправлять обновления в UI
    pipeline = FullAnalysisPipeline(ui_queue, config.get_full_config())

    # Инициализируем фронтенд (GUI)
    # Передаем ui_queue, чтобы UI мог получать обновления
    # Передаем методы бэкенда, которые может вызывать UI (start/stop)
    root = tk.Tk()
    gui_app = GUIApp(
        master=root,
        backend_pipeline=pipeline,
        ui_queue=ui_queue,
        initial_config=config.get_full_config(),
    )

    # Запускаем GUI
    # tk.mainloop() будет блокировать, пока окно GUI открыто
    root.mainloop()

    # После закрытия GUI, убеждаемся, что рабочий поток бэкенда остановлен
    pipeline.stop_analysis()
    if pipeline.worker_thread and pipeline.worker_thread.is_alive():
        pipeline.worker_thread.join()
        print("Backend thread joined cleanly.")


if __name__ == "__main__":
    main()
