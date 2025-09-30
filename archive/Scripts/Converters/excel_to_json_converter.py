# excel_to_json_converter.py
import pandas as pd
import json
from collections import defaultdict

# --- НАСТРОЙКИ ---
INPUT_EXCEL_FILE = 'adgs.xlsx'
OUTPUT_JSON_FILE = 'database.json' # Этот файл будет лежать рядом с вашим html

# --- КОЛОНКИ (убедитесь, что названия совпадают с вашими) ---
COL_ID = "ID"
COL_GO = "Исполняющий ГО"
COL_PARENT_GO = "Вышестоящий ГО"
COL_TEXT = "FunctionText"
COL_LEVEL = "Level" # Уровень ГО (1, 2, 3...)
# Добавьте остальные колонки, которые хотите видеть в дашборде
# COL_TYPE = "Type"
# COL_SPHERE_1 = "Sphere_1"
# ... и так далее

def build_hierarchy(df):
    """Строит иерархическое дерево госорганов с учетом самореференций у ГО верхнего уровня."""
    tree = {"name": "Правительство", "children": []}
    nodes = {"Правительство": tree}

    # Шаг 1: Создаем все узлы для каждого уникального госоргана
    for go_name in df[COL_GO].unique():
        if go_name and go_name not in nodes:
            nodes[go_name] = {"name": go_name, "children": [], "functions": []}

    # Шаг 2: Строим связи между узлами с новой логикой
    for _, row in df.iterrows():
        go_name = row[COL_GO]
        parent_name_from_col = row[COL_PARENT_GO]

        # Новая логика: если 'Вышестоящий ГО' пуст ИЛИ равен 'Исполняющий ГО',
        # то считаем его органом верхнего уровня и привязываем к "Правительству".
        if not parent_name_from_col or go_name == parent_name_from_col:
            parent_name = "Правительство"
        else:
            parent_name = parent_name_from_col

        # Убеждаемся, что оба узла существуют, прежде чем создавать связь
        if go_name in nodes and parent_name in nodes:
            node = nodes[go_name]
            parent_node = nodes[parent_name]

            # Добавляем в дочерние, только если его там еще нет
            if node not in parent_node["children"]:
                parent_node["children"].append(node)

    return tree


def main():
    print(f"Чтение файла: {INPUT_EXCEL_FILE}")
    try:
        df = pd.read_excel(INPUT_EXCEL_FILE).fillna('')
    except FileNotFoundError:
        print(f"ОШИБКА: Файл не найден по пути {INPUT_EXCEL_FILE}")
        return

    # 1. Создаем карту функций для быстрого доступа по ID
    #    Это будет наш основной справочник данных по каждой функции.
    functions_map = {}
    for _, row in df.iterrows():
        func_id = str(row[COL_ID])
        functions_map[func_id] = row.to_dict()

    # 2. Добавляем список ID функций к каждому ГО в иерархии
    hierarchy_tree = build_hierarchy(df)

    # Собираем ID функций для каждого ГО
    go_to_funcs = defaultdict(list)
    for func_id, func_data in functions_map.items():
        go_name = func_data.get(COL_GO)
        if go_name:
            go_to_funcs[go_name].append(func_id)

    # Рекурсивная функция для добавления ID функций в дерево
    def add_functions_to_tree(node):
        node_name = node["name"]
        if node_name in go_to_funcs:
            node["functions"] = go_to_funcs[node_name]
        for child in node["children"]:
            add_functions_to_tree(child)

    add_functions_to_tree(hierarchy_tree)


    # 3. Собираем все в один итоговый JSON
    final_database = {
        "hierarchy": hierarchy_tree,
        "functions": functions_map
    }

    # 4. Сохраняем в файл
    with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_database, f, ensure_ascii=False, indent=2)

    print(f"✅ Готово! Данные успешно сохранены в {OUTPUT_JSON_FILE}")

if __name__ == '__main__':
    main()