from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .gui.parser_app import ParserApp
from .gui.typing_app import FunctionTypologyApp
from .gui.spheres_app import SphereClassifierApp
from .gui.grouping_app import CollisionAnalyzerApp


class Launcher:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Аналитические инструменты")
        self.root.geometry("400x250")
        self._build_ui()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Выберите инструмент:").pack(anchor="w")
        ttk.Button(
            frame,
            text="Парсер функций ГО",
            command=self.launch_parser,
            style="Accent.TButton",
        ).pack(fill=tk.X, pady=10)
        ttk.Button(
            frame,
            text="Типологизация функций",
            command=self.launch_typology,
        ).pack(fill=tk.X, pady=10)
        ttk.Button(
            frame,
            text="Классификация по сферам",
            command=self.launch_spheres,
        ).pack(fill=tk.X, pady=10)
        ttk.Button(
            frame,
            text="Анализ коллизий",
            command=self.launch_grouping,
        ).pack(fill=tk.X, pady=10)
        ttk.Button(
            frame,
            text="Выход",
            command=self.root.destroy,
        ).pack(fill=tk.X, pady=10)

    def launch_parser(self) -> None:
        ParserApp(tk.Toplevel(self.root))

    def launch_typology(self) -> None:
        FunctionTypologyApp(tk.Toplevel(self.root))

    def launch_spheres(self) -> None:
        SphereClassifierApp(tk.Toplevel(self.root))

    def launch_grouping(self) -> None:
        CollisionAnalyzerApp(tk.Toplevel(self.root))

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    Launcher().run()


if __name__ == "__main__":
    main()
