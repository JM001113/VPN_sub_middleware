import re
from abc import ABC
from collections.abc import Callable


class BaseCriterion(ABC):
    def __init__(self, searching_function: Callable[[str], bool]):
        self._search_fun = searching_function

    def __call__(self, vless_string) -> bool:
        return self._search_fun(vless_string)


class NameCriterion(BaseCriterion):
    def __init__(self, name_criteria: str, regex: bool = False):
        self.regex = regex
        if self.regex:
            self.name_criteria: re.Pattern | str = re.compile(name_criteria)
        else:
            self.name_criteria: re.Pattern | str = name_criteria
        super().__init__(self._searcher)

    def _searcher(self, vless_string):
        if self.regex:
            return bool(self.name_criteria.search(vless_string))
        return bool(self.name_criteria in vless_string)


def name_criterion(vless_link: str, /, *, name_criteria: str):
    name = vless_link[vless_link.find("#") + 1:]
    if name_criteria in name:
        return True
    return False
