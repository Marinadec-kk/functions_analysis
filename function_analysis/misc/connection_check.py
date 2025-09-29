#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Итоговый тестовый скрипт (болванка) v2.1 для проверки соединения с LLM-серверами
================================================================================
Автор: Gemini
Версия: 2.1 (28.08.2025)

Этот скрипт позволяет протестировать три разных режима подключения и измерить
скорость генерации токенов (T/s).

Режимы:
1.  OPENAI:       Официальное API OpenAI (нужен только ключ).
2.  LOCAL:        Локальный сервер без ключа (LM Studio, Ollama).
3.  CUSTOM_API:   Сторонний сервер, требующий и URL, и API-ключ.

Для использования просто выберите нужный TEST_MODE и заполните
соответствующие ему настройки.
"""

import os
import sys
import time
# --- ЗАВИСИМОСТИ ---
try:
    import openai
except ImportError:
    print("="*80)
    print("ОШИБКА: Не найдена библиотека openai.")
    print("Пожалуйста, установите ее командой: pip install --upgrade openai")
    print("="*80)
    sys.exit(1)

# -------------------------------------------------------------------
# ---                ГЛАВНАЯ НАСТРОЙКА РЕЖИМА                     ---
# -------------------------------------------------------------------
# Измените значение этой переменной, чтобы выбрать режим теста:
# "OPENAI", "LOCAL" или "CUSTOM_API"
TEST_MODE = "CUSTOM_API"

# -------------------------------------------------------------------
# ---         НАСТРОЙКИ ДЛЯ КАЖДОГО РЕЖИМА                        ---
# --- ЗАПОЛНИТЕ ДАННЫЕ ДЛЯ ТОГО РЕЖИМА, КОТОРЫЙ ВЫ ВЫБРАЛИ       ---
# -------------------------------------------------------------------

# --- 1. Настройки для режима "OPENAI" ---
OPENAI_CONFIG = {
    "api_key": os.getenv("OPENAI_API_KEY", "sk-..."), # Вставьте ваш OpenAI ключ
    "model": "gpt-4-turbo"
}

# --- 2. Настройки для режима "LOCAL" (локальный сервер без ключа) ---
LOCAL_CONFIG = {
    "base_url": "http://10.203.99.103:1234", # Адрес вашего локального сервера
    "model": "local-model"
}

# --- 3. Настройки для режима "CUSTOM_API" (сторонний сервер с ключом) ---
CUSTOM_API_CONFIG = {
    "base_url": "https://llm.govplan.kz", # URL вашего кастомного сервера
    "api_key": os.getenv("MY_CUSTOM_API_KEY", "sk-..."), # Ключ для этого сервера
    "model": "openai/gpt-oss-20b"
}

# -------------------------------------------------------------------

def prepare_api_base_url(url: str) -> str:
    """Готовит URL для OpenAI-совместимого API."""
    if not url:
        return None
    url = url.strip().rstrip('/')
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    if not url.endswith('/v1'):
        url += "/v1"
    return url

def main():
    """Основная функция для выполнения теста."""
    print(f"--- НАЧАЛО ТЕСТА СОЕДИНЕНИЯ (РЕЖИМ: {TEST_MODE}) ---")

    api_key = None
    base_url = None
    model_name = None

    # 1. Выбираем конфигурацию в зависимости от режима
    if TEST_MODE == "OPENAI":
        config = OPENAI_CONFIG
        api_key = config["api_key"]
        model_name = config["model"]
        print(f"1. Целевой сервер: OpenAI API (по умолчанию)")
        print(f"2. Модель: {model_name}")

    elif TEST_MODE == "LOCAL":
        config = LOCAL_CONFIG
        base_url = prepare_api_base_url(config["base_url"])
        model_name = config["model"]
        api_key = "not-needed" # Ключ не используется
        print(f"1. Целевой сервер: {base_url}")
        print(f"2. Модель: {model_name}")

    elif TEST_MODE == "CUSTOM_API":
        config = CUSTOM_API_CONFIG
        base_url = prepare_api_base_url(config["base_url"])
        api_key = config["api_key"]
        model_name = config["model"]
        print(f"1. Целевой сервер: {base_url}")
        print(f"2. Модель: {model_name}")

    else:
        print(f"[ОШИБКА] Неизвестный режим '{TEST_MODE}'. Выберите 'OPENAI', 'LOCAL' или 'CUSTOM_API'.")
        sys.exit(1)

    print(f"3. API-ключ: {'Используется' if api_key and api_key != 'not-needed' else 'Не используется'}")

    # 2. Создание клиента OpenAI
    try:
        client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=60.0,
        )
        print("\n[OK] Клиент OpenAI успешно создан.")
    except Exception as e:
        print(f"\n[ОШИБКА] Не удалось создать клиент OpenAI: {e}")
        sys.exit(1)

    # 3. Формирование и отправка тестового запроса
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Tell me a short joke about programming."}
    ]

    print("\n2. Отправка тестового запроса на сервер...")

    try:
        # Замеряем время начала
        start_time = time.monotonic()

        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=0.7,
            max_tokens=100
        )

        # Замеряем время окончания
        end_time = time.monotonic()
        duration = end_time - start_time

        print("\n[УСПЕХ] Сервер успешно ответил!")

        answer = response.choices[0].message.content.strip()
        print("-" * 50)
        print("ОТВЕТ МОДЕЛИ:")
        print(answer)
        print("-" * 50)

        # Печатаем информацию о токенах, если она доступна
        if response.usage:
            completion_tokens = response.usage.completion_tokens
            print(f"Потрачено токенов: {response.usage.total_tokens} (prompt: {response.usage.prompt_tokens}, completion: {completion_tokens})")

            # Рассчитываем и выводим T/s
            if duration > 0 and completion_tokens is not None:
                tps = completion_tokens / duration
                print(f"Скорость генерации: {tps:.2f} T/s")

        print("\n--- ТЕСТ УСПЕШНО ЗАВЕРШЕН ---")

    except openai.AuthenticationError as e:
        print("\n[ОШИБКА АУТЕНТИФИКАЦИИ]")
        print("Сервер отклонил ваш API-ключ. Проверьте его правильность.")
        print(f"Детали ошибки: {e}")

    except openai.APIConnectionError as e:
        print("\n[ОШИБКА СОЕДИНЕНИЯ]")
        print(f"Не удалось подключиться к серверу по адресу: {base_url}")
        print("Проверьте, что адрес указан верно, сервер запущен и доступен.")
        print(f"Детали ошибки: {e}")

    except openai.NotFoundError as e:
        print(f"\n[ОШИБКА: МОДЕЛЬ НЕ НАЙДЕНА]")
        print(f"Сервер не нашел модель с именем '{model_name}'. Проверьте имя.")
        print(f"Детали ошибки: {e}")

    except openai.APIStatusError as e:
        print(f"\n[ОШИБКА API СТАТУСА: {e.status_code}]")
        print(f"Сервер вернул ошибку: {e.response.text}")

    except Exception as e:
        print(f"\n[НЕИЗВЕСТНАЯ ОШИБКА]")
        print(f"Произошла непредвиденная ошибка: {e}")

if __name__ == "__main__":
    main()