from pathlib import Path
import sqlite3


class SystemStatus:
    def __init__(self):
        self.database_path = Path("data/paper_simulation.db")

    def get_status(self):
        status = {
            "python": "🟢 Running",
            "database": "🔴 Not Found",
            "orders": 0,
            "filled": 0,
            "pending": 0,
        }

        if not self.database_path.exists():
            return status

        status["database"] = "🟢 Connected"

        try:
            conn = sqlite3.connect(self.database_path)
            cur = conn.cursor()

            cur.execute("SELECT COUNT(*) FROM execution_history")
            status["orders"] = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM execution_history WHERE order_status='reconciled'"
            )
            status["filled"] = cur.fetchone()[0]

            cur.execute(
                "SELECT COUNT(*) FROM execution_history WHERE order_status!='reconciled'"
            )
            status["pending"] = cur.fetchone()[0]

            conn.close()

        except Exception:
            status["database"] = "🔴 Error"

        return status
    def database_connected(self):
        return self.get_status()["database"] == "🟢 Connected"

    def total_orders(self):
        return self.get_status()["orders"]

    def reconciled_orders(self):
        return self.get_status()["filled"]

    def pending_orders(self):
        return self.get_status()["pending"]
