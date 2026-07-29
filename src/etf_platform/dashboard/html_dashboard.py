from pathlib import Path
import json


class HTMLDashboard:
    """
    Generates an interactive HTML dashboard.
    """

    def generate(
        self,
        report,
        equity_curve=None,
        ai_report=None,
        output_file="reports/dashboard.html",
    ):
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        summary = report["summary"]

        if equity_curve is None:
            equity_curve = []

        chart_data = json.dumps(equity_curve)

        ai_html = ""
        if ai_report:
            ai = ai_report["summary"]
            ai_html = f"""
<div class="card">
<h2>AI Decision Summary</h2>
<table>
<tr><td>Health Score</td><td>{ai["health_score"]}</td></tr>
<tr><td>Risk Level</td><td>{ai["risk_level"]}</td></tr>
<tr><td>Market</td><td>{ai["market_condition"]}</td></tr>
<tr><td>Strategy</td><td>{ai["recommended_strategy"]}</td></tr>
</table>
</div>
"""

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>ETF Platform Dashboard</title>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>
body {{
    font-family: Arial, sans-serif;
    margin: 40px;
    background: #f5f5f5;
}}

.card {{
    background: white;
    padding: 20px;
    border-radius: 8px;
    margin-bottom: 20px;
}}

table {{
    width: 100%;
    border-collapse: collapse;
}}

th, td {{
    border: 1px solid #ddd;
    padding: 10px;
}}

canvas {{
    max-height: 400px;
}}
</style>
</head>

<body>

<div class="card">
<h1>ETF Platform Dashboard</h1>

<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>CAGR</td><td>{summary["cagr_percent"]}%</td></tr>
<tr><td>XIRR</td><td>{summary["xirr_percent"]}%</td></tr>
<tr><td>Max Drawdown</td><td>{summary["max_drawdown_percent"]}%</td></tr>
<tr><td>Volatility</td><td>{summary["annualized_volatility_percent"]}%</td></tr>
<tr><td>Sharpe Ratio</td><td>{summary["sharpe_ratio"]}</td></tr>
<tr><td>Sortino Ratio</td><td>{summary["sortino_ratio"]}</td></tr>
<tr><td>Calmar Ratio</td><td>{summary["calmar_ratio"]}</td></tr>
</table>

</div>

{ai_html}

<div class="card">
<h2>Equity Curve</h2>
<canvas id="equityChart"></canvas>
</div>

<script>
const equityData = {chart_data};

new Chart(
document.getElementById("equityChart"),
{{
type:"line",
data:{{
labels:equityData.map((_,i)=>i+1),
datasets:[{{
label:"Portfolio Value",
data:equityData
}}]
}}
}});
</script>

</body>
</html>
"""

        output_path.write_text(html, encoding="utf-8")
        return output_path
