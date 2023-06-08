#region imports
from AlgorithmImports import *
#endregion

import fred_data_2000_2006, fred_data_2007_2023
from io import StringIO
import pandas as pd

fred_csv_data = """
date,ir
"""

fred_csv_data += fred_data_2000_2006.fred_csv_data + fred_data_2007_2023.fred_csv_data

# Parse the csv data
fred = pd.read_csv(StringIO(fred_csv_data), parse_dates = ["date"])
