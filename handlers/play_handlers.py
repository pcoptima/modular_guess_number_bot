from aiogram import types
from aiogram.fsm.context import FSMContext
from typing import Dict, Any, List
from random import randint
from lexicon import LEXICON
from states import GameStates
from keyboards import in_game_menu_keyboard, settings_menu_keyboard, main_menu_keyboard, my_settings_menu_keyboard
from db import (save_game_data, save_user_state, increment_games_lost,
                get_max_game_id, set_attempts_left, user_settings_to_dict,
                decrement_attempts_left, get_time_since_game_start, load_user_settings,
                increment_games_won)
import asyncio  # Добавлено для работы с таймером


async def check_missing_settings(data: Dict[str, Any]) -> List[str]:
    """
    Проверяет, какие настройки отсутствуют в данных состояния.

    Аргументы:
        data (Dict[str, Any]): Данные состояния пользователя.

    Возвращает:
        List[str]: Список отсутствующих настроек.
    """
    missing_settings = []
    if data:
        if data['range_start'] is None or data['range_end'] is None:
            missing_settings.append("Диапазон чисел")
        if data['time_limit'] is None:
            missing_settings.append("Время")
        if data['attempts'] is None:
            missing_settings.append("Попыток")
    return missing_settings


async def start_game_timer(state: FSMContext, user_id: int, data: Dict[str, Any], callback_query: types.CallbackQuery) -> None:
    """
    Запускает таймер игры. Если время истекает, завершает игру.

    Аргументы:
        state (FSMContext): Контекст состояния FSM.
        user_id (int): ID пользователя.
        data (Dict[str, Any]): Данные состояния пользователя.
        callback_query (types.CallbackQuery): Объект callback-запроса.
    """
    async def game_timer():
        await asyncio.sleep(data['time_limit'])
        current_state = await state.get_state()
        if current_state == GameStates.game:
            await _game_lost(user_id, state)
            await callback_query.message.answer(
                LEXICON["game_lost_time"].format(
                    time_limit=data['time_limit'])
            )
    asyncio.create_task(game_timer())


async def _game_lost(user_id: int, state: FSMContext) -> None:
    """
    Обрабатывает проигрыш пользователя.

    Аргументы:
        user_id (int): ID пользователя.
        state (FSMContext): Контекст состояния FSM.
    """
    game_id = await get_max_game_id(user_id)
    await save_game_data(game_id=game_id, user_id=user_id, results="lost")
    await state.set_state(GameStates.out_game)
    await save_user_state(user_id, "out_game")
    await increment_games_lost(user_id)


async def _game_won(user_id: int, state: FSMContext) -> None:
    """
    Обрабатывает победу пользователя.

    Аргументы:
        user_id (int): ID пользователя.
        state (FSMContext): Контекст состояния FSM.
    """
    game_id = await get_max_game_id(user_id)
    await save_game_data(game_id=game_id, user_id=user_id, results="won")
    await state.set_state(GameStates.out_game)
    await save_user_state(user_id, "out_game")
    await increment_games_won(user_id)


async def initialize_game(callback_query: types.CallbackQuery, state: FSMContext, data: Dict[str, Any]) -> None:
    """
    Инициализирует игру: устанавливает состояние, сохраняет целевое число,
    запускает таймер и отправляет сообщение с приглашением к игре.

    Аргументы:
        callback_query (types.CallbackQuery): Объект callback-запроса.
        state (FSMContext): Контекст состояния FSM.
        data (Dict[str, Any]): Данные состояния пользователя.
    """
    user_id = callback_query.from_user.id
    await state.set_state(GameStates.game)
    await save_user_state(user_id, "game")
    target_number = randint(data['range_start'], data['range_end'])
    if user_id is None:
        print("Ошибка: не удалось определить ID пользователя.")
        return
    await state.update_data(target_number=target_number)
    await save_game_data(user_id=user_id, target_number=target_number)
    await start_game_timer(state, user_id, data, callback_query)
    attempts_left = await set_attempts_left(user_id)
    await callback_query.message.answer(
        LEXICON["play_prompt"].format(
            attempts_left=attempts_left
        ),
        reply_markup=in_game_menu_keyboard()
    )
    await callback_query.answer()


async def process_play(callback_query: types.CallbackQuery, state: FSMContext) -> None:
    """
    Основной метод для обработки команды начала игры.

    Аргументы:
        callback_query (types.CallbackQuery): Объект callback-запроса.
        state (FSMContext): Контекст состояния FSM.
    """
    user_id = callback_query.from_user.id
    user_settings = await user_settings_to_dict(user_id)
    if user_settings:
        await state.update_data(**user_settings)
    else:
        await callback_query.message.answer(LEXICON["my_settings_error"], reply_markup=my_settings_menu_keyboard())
        await callback_query.answer()
        return
    data = await state.get_data()
    missing_settings = await check_missing_settings(data)
    if missing_settings:
        await callback_query.message.answer(
            LEXICON["missing_settings"].format(
                missing_settings=", ".join(missing_settings)),
            reply_markup=settings_menu_keyboard()
        )
        await callback_query.answer()
    else:
        await initialize_game(callback_query, state, data)


async def main_process_play(message: types.Message, state: FSMContext) -> None:
    """
    Обрабатывает ввод пользователя во время игры.

    Аргументы:
        message (types.Message): Сообщение пользователя.
        state (FSMContext): Контекст состояния FSM.
    """
    user_id = message.from_user.id
    user_number = int(message.text)
    data = await state.get_data()
    target_number = data.get('target_number')
    attempts_left = await decrement_attempts_left(user_id)
    if attempts_left == 0:
        await _game_lost(user_id, state)
        await message.answer(LEXICON["game_lost_attempts"], reply_markup=main_menu_keyboard())
        return
    if user_number < target_number:
        await message.answer(LEXICON["my_number_is_higher"].format(
            attempts_left=attempts_left
        ), reply_markup=in_game_menu_keyboard())
    elif user_number == target_number:
        all_data_user = await load_user_settings(user_id)
        attempts = all_data_user[3]
        await _game_won(user_id, state)
        seconds_passed = await get_time_since_game_start(user_id)
        await message.answer(LEXICON["won_game"].format(
            attempts_left=attempts - attempts_left,
            seconds_passed=seconds_passed
        ), reply_markup=main_menu_keyboard())
    else:
        await message.answer(LEXICON["my_number_is_less"].format(
            attempts_left=attempts_left
        ), reply_markup=in_game_menu_keyboard())
