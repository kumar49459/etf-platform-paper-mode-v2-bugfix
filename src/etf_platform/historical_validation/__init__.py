"""Historical Validation (Milestone 5A). See
docs/MILESTONE_5A_Historical_Validation_Design.md for the full design.

CRITICAL: this environment has no bulk historical price data access
(confirmed by direct testing -- see verified_etf_records.py's capability
correction). Fact-level web search/fetch access DOES work and was used
to verify ETF inception dates and benchmarks. Any historical run of this
package uses either real data supplied by the caller through
CSVDataProvider, or clearly-labeled SYNTHETIC data (synthetic_data.py)
used only to validate the framework itself. See provenance.py's
DataSource enum -- every report this package produces refuses to omit a
disclosure banner whenever synthetic data is involved.
"""

from etf_platform.historical_validation.csv_data_provider import CSVDataProvider
from etf_platform.historical_validation.data_acquisition_service import (
    HistoricalDataAcquisitionService,
    NoProviderForRangeError,
)
from etf_platform.historical_validation.provider_decorators import IndexProxyDataProvider, ValidatedDataProvider
