from abc import ABC, abstractmethod


class Dataset(ABC):
    name: str
    db_path: str
    description: str
    enabled: bool = True

    @abstractmethod
    def schema_summary(self) -> str:
        """Return a compact text description of schema for LLM
        """
        ...
