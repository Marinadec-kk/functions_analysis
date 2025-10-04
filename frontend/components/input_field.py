import tkinter as tk
from tkinter import ttk, filedialog


def create_input_field(
    parent_frame: ttk.Frame,
    label_text: str,
    config_key: str,
    entry_widgets: dict,
    is_folder: bool = False,
    file_ext: str = "",
    default_value: str = "",
):
    """
    Creates a labeled input field with a browse button.

    Args:
        parent_frame (ttk.Frame): The parent frame to contain the widget.
        label_text (str): The text for the label.
        config_key (str): The key to use for storing the widget's variable.
        entry_widgets (dict): A dictionary to store the entry widget's variable.
        is_folder (bool, optional): True if the dialog should select a folder. Defaults to False.
        file_ext (str, optional): The file extension to filter for. Defaults to "".
        default_value (str, optional): The default value for the entry field. Defaults to "".
    """
    ttk.Label(parent_frame, text=label_text).pack(padx=5, pady=5, anchor="w")
    entry_var = tk.StringVar(value=default_value)
    entry = ttk.Entry(parent_frame, width=80, textvariable=entry_var)
    entry.pack(padx=5, pady=2, fill="x")
    entry_widgets[config_key] = entry_var

    if is_folder:
        button_command = lambda: _choose_directory(entry_var)
    else:
        button_command = lambda: _choose_file(entry_var, file_ext)

    ttk.Button(parent_frame, text="Выбрать", command=button_command).pack(
        padx=5, pady=2, anchor="w"
    )


def _choose_directory(entry_var: tk.StringVar):
    """Opens a dialog to choose a directory."""
    folder_selected = filedialog.askdirectory()
    if folder_selected:
        entry_var.set(folder_selected)


def _choose_file(entry_var: tk.StringVar, file_ext: str):
    """Opens a dialog to choose a file."""
    file_types = []
    if file_ext == ".xlsx":
        file_types.append(("Excel files", "*.xlsx"))
        file_types.append(("CSV files", "*.csv"))
    elif file_ext == ".json":
        file_types.append(("JSON files", "*.json"))
    else:
        file_types.append(("All files", "*.*"))

    file_selected = filedialog.askopenfilename(filetypes=file_types)
    if file_selected:
        entry_var.set(file_selected)
