from abc import ABC
from abc import abstractmethod

import pandas as pd


class DataSource(ABC):
    @abstractmethod
    def load(self) -> pd.DataFrame:
        raise NotImplementedError
