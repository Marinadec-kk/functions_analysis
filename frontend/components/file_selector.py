import tkinter as tk
from tkinter import ttk
import os


def create_file_selector(
    parent_frame: ttk.Frame,
    label_text: str,
    config_key: str,
    entry_widgets: dict,
    directory: str,
    file_extension: str = ".xlsx",
):
    """
    Creates a labeled dropdown (Combobox) to select a file from a given directory.

    Args:
        parent_frame (ttk.Frame): The parent frame to contain the widget.
        label_text (str): The text for the label.
        config_key (str): The key to use for storing the widget's variable.
        entry_widgets (dict): A dictionary to store the Combobox widget's variable.
        directory (str): The directory to scan for files.
        file_extension (str): The file extension to filter by.
    """
    ttk.Label(parent_frame, text=label_text).pack(padx=5, pady=5, anchor="w")

    combo_var = tk.StringVar()
    combobox = ttk.Combobox(parent_frame, textvariable=combo_var, width=78)
    combobox.pack(padx=5, pady=2, fill="x")
    entry_widgets[config_key] = combo_var

    def update_file_list():
        """Scans the directory and updates the combobox list."""
        try:
            if os.path.exists(directory):
                files = [f for f in os.listdir(directory) if f.endswith(file_extension)]
                combobox["values"] = files
                if files:
                    combo_var.set(files[0])  # Set default selection
            else:
                combobox["values"] = []
                combo_var.set(f"Папка не найдена: {directory}")
        except Exception as e:
            combo_var.set(f"Ошибка чтения папки: {e}")

    # Refresh button
    ttk.Button(parent_frame, text="Обновить", command=update_file_list).pack(
        padx=5, pady=2, anchor="w"
    )

    # Initial population
    update_file_list()
