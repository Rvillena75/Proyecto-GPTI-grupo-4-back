"""Business failures translated to JSON by the API layer."""


class RuleViolation(Exception):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)
