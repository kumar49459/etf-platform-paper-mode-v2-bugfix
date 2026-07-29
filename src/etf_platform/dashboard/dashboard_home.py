from pathlib import Path


class DashboardHome:
    """
    Generates the dashboard home page.
    """

    def generate(self, report, output_file="reports/index.html"):
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        summary = report["summary"]

        cards = [
            ("CAGR", f'{summary["cagr_percent"]}%'),
            ("XIRR", f'{summary["xirr_percent"]}%'),
            ("Sharpe", summary["sharpe_ratio"]),
            ("Sortino", summary["sortino_ratio"]),
            ("Calmar", summary["calmar_ratio"]),
            ("Max Drawdown", f'{summary["max_drawdown_percent"]}%'),
        ]

        html = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>ETF Platform</title>

<style>

body{
font-family:Arial;
background:#f4f6f9;
margin:40px;
}

.grid{
display:grid;
grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
gap:20px;
}

.card{
background:white;
padding:20px;
border-radius:10px;
box-shadow:0 2px 8px rgba(0,0,0,.1);
}

.value{
font-size:30px;
font-weight:bold;
margin-top:10px;
}

</style>

</head>

<body>

<h1>ETF Platform Dashboard</h1>

<div class="grid">
"""

        for title, value in cards:
            html += f"""
<div class="card">
<h3>{title}</h3>
<div class="value">{value}</div>
</div>
"""

        html += """
</div>

<p style="margin-top:40px">
<a href="dashboard.html">Open Detailed Dashboard</a>
</p>

</body>
</html>
"""

        output_path.write_text(
            html,
            encoding="utf-8",
        )

        return output_path
