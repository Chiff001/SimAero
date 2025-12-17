import os


class AirLogger:
    created_files = []

    def __init__(self, *file_names: str, prefix: str = ""):
        self.prefix = prefix

        self.file_names = []
        for file_name in file_names:
            if file_name == ".print":
                self.file_names.append(file_name)
                continue
            new_file_name = "logs/" + file_name + ".txt"
            self.file_names.append(new_file_name)

            if new_file_name not in AirLogger.created_files:
                AirLogger.created_files.append(new_file_name)
                with open(new_file_name, 'w', encoding='utf-8') as _:
                    pass

    def log(self, string: str):
        for file_name in self.file_names:
            if file_name == ".print":
                print(string if self.prefix == "" else self.prefix + ":" + string)
                continue

            with open(file_name, 'a', encoding='utf-8') as f:
                f.write(f"{string}\n" if self.prefix == "" else f"{self.prefix}:{string}\n")
