#region imports
from AlgorithmImports import *
#endregion

########################################################################################
#                                                                                      #
# Licensed under the Apache License, Version 2.0 (the "License");                      #
# you may not use this file except in compliance with the License.                     #
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0   #
#                                                                                      #
# Unless required by applicable law or agreed to in writing, software                  #
# distributed under the License is distributed on an "AS IS" BASIS,                    #
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.             #
# See the License for the specific language governing permissions and                  #
# limitations under the License.                                                       #
#                                                                                      #
# Copyright [2021] [Rocco Claudio Cannizzaro]                                          #
#                                                                                      #
########################################################################################

import numpy as np
import pandas as pd
import time as timer
from System.Drawing import Color
from Strategies import *
from Logger import *

class StrategyBacktest(QCAlgorithm):

   # #####################################
   #    Backtesting parameters
   # #####################################
   def Initialize(self):
      # Backtesting period
      self.SetStartDate(2021, 1, 1)
      self.SetEndDate(2021, 11, 30)
      # Store the initial account value
      self.initialAccountValue = 1000000
      self.SetCash(self.initialAccountValue)
      
      # Logging level: 
      #  -> 0 = ERROR
      #  -> 1 = WARNING
      #  -> 2 = INFO
      #  -> 3 = DEBUG
      #  -> 4 = TRACE (Attention!! This can consume your entire daily log limit)
      self.logLevel = 2
      
      # Ticker Symbol
      self.ticker = "SPX"
      
      # Days to Expiration
      self.dte = 45
      # The size of the window used to filter the option chain: options expiring in the range [dte-dteWindow, dte] will be selected
      self.dteWindow = 7
      
      # Risk Free Rate for the Black-Scholes-Merton model
      self.riskFreeRate = 0.001

      # Use Limit Orders to open/close a position?
      self.useLimitOrders = True
      
      # Slippage used to set Limit orders
      self.slippage = 0.05
            
      # Adjustment factor applied to the Mid-Price to set the Limit Order:
      #  - Credit Strategy:
      #      Adj = 0.3 --> sets the Limit Order price 30% higher than the current Mid-Price
      #  - Debit Strategy:
      #      Adj = -0.2 --> sets the Limit Order price 20% lower than the current Mid-Price
      self.limitOrderRelativePriceAdjustment = 0.2
      
      # Alternative method to set the absolute price (per contract) of the Limit Order. This method is used if a number is specified
      # Unless you know that your price target can get a fill, it is advisable to use a relative adjustment or you may never get your order filled 
      #  - Credit Strategy:
      #      AbsolutePrice = 1.5 --> sets the Limit Order price at exactly 1.5$
      #  - Debit Strategy:
      #      AbsolutePrice = -2.3 --> sets the Limit Order price at exactly -2.3$
      # self.limitOrderAbsolutePrice = 2.1
      
      # Set expiration for Limit orders
      self.limitOrderExpiration = timedelta(hours = 4)
      
      # Target <credit|debit> premium amount: used to determine the number of contracts needed to reach the desired target amount
      #  - targetPremiumPct --> target premium is expressed as a percentage of the total Portfolio Net Liq (0 < targetPremiumPct < 1)
      #  - targetPremium --> target premium is a fixed dollar amount
      # If both are specified, targetPremiumPct takes precedence. If none of them are specified, the number of contracts specified by the maxOrderQuantity parameter is used.
      self.targetPremiumPct = None
      self.targetPremium = 1000

      # Maximum quantity used to scale each position. If the target premium cannot be reached within this quantity (i.e. premium received is too low), the position is not going to be opened
      self.maxOrderQuantity = 20
      # If True, the order is submitted as long as it does not exceed the maxOrderQuantity.
      self.validateQuantity = True
      
      # Profit Target Factor (Multiplier of the premium received/paid when the position was opened)
      self.profitTarget = 0.6

      # Defines how the profit target is calculated. Valid options are (case insensitive):
      # - Premium: the profit target is a percentage of the premium paid/received. 
      # - Theta: the profit target is calculated based on the theta value of the position evaluated at self.thetaProfitDays from the time of entering the trade
      # - TReg: the profit target is calculated as a percentage of the TReg (MaxLoss + openPremium)
      # - Margin: the profit target is calculted as a percentage of the margin requirement (calculated based on self.portfolioMarginStress percentage upside/downside movement of the underlying)
      self.profitTargetMethod = "Premium"
      # Number of days into the future at which the theta of the position is calculated. Used if profitTargetMethod = "Theta"
      self.thetaProfitDays = None
      # Upside/Downside stress applied to the underlying to calculate the portfolio margin requirement of the position
      self.portfolioMarginStress = 0.12

      
      # Stop Loss Multiplier, expressed as a function of the profit target (rather than the credit received)
      # The position is closed (Market Order) if:
      #    Position P&L < -abs(openPremium) * stopLossMultiplier
      # where:
      #  - openPremium is the premium received (positive) in case of credit strategies
      #  - openPremium is the premium paid (negative) in case of debit strategies
      #
      # Credit Strategies (i.e. $2 credit):
      #  - profitTarget < 1 (i.e. 0.5 -> 50% profit target -> $1 profit)
      #  - stopLossMultiplier = 2 * profitTarget (i.e. -abs(openPremium) * stopLossMultiplier = -abs(2) * 2 * 0.5 = -2 --> stop if P&L < -2$)
      # Debit Strategies (i.e. $4 debit):
      #  - profitTarget < 1 (i.e. 0.5 -> 50% profit target -> $2 profit)
      #  - stopLossMultiplier < 1 (You can't lose more than the debit paid. i.e. stopLossMultiplier = 0.6 --> stop if P&L < -2.4$)
      self.stopLossMultiplier = 2 * self.profitTarget
      #self.stopLossMultiplier = 0.6
      
      # DTE Threshold. This is ignored if self.dte < self.dteThreshold
      self.dteThreshold = None
      # DIT Threshold. This is ignored if self.dte < self.ditThreshold
      self.ditThreshold = None
      self.hardDitThreshold = None
      
      # Controls what happens when an open position reaches/crosses the dteThreshold ( -> DTE(openPosition) <= dteThreshold)
      # - If True, the position is closed as soon as the dteThreshold is reached, regardless of whether the position is profitable or not
      # - If False, once the dteThreshold is reached, the position is closed as soon as it is profitable
      self.forceDteThreshold = False
      # Controls what happens when an open position reaches/crosses the ditThreshold ( -> DIT(openPosition) >= ditThreshold)
      # - If True, the position is closed as soon as the ditThreshold is reached, regardless of whether the position is profitable or not
      # - If False, once the ditThreshold is reached, the position is closed as soon as it is profitable
      # - If self.hardDitThreashold is set, the position is closed once the hardDitThreashold is crossed, regardless of whether forceDitThreshold is True or False
      self.forceDitThreshold = False
            
      # Maximum number of open positions at any given time
      self.maxActivePositions = 20

      # If True, the order mid-price is validated to make sure the Bid-Ask spread is not too wide.
      #  - The order is not submitted if the ratio between Bid-Ask spread of the entire order and its mid-price is more than self.bidAskSpreadRatio
      self.validateBidAskSpread = False
      self.bidAskSpreadRatio = 0.8

      #Controls whether to include Cancelled orders (Limit orders that didn't fill) in the final output
      self.includeCancelledOrders = False

      # Controls whether to allow multiple positions to be opened for the same Expiration date
      self.allowMultipleEntriesPerExpiry = False
      
      # Controls whether to include details on each leg (open/close fill price and descriptive statistics about mid-price, Greeks, and IV)
      self.includeLegDetails = False
      # Specify which Greeks should be included in the trade log (Set an empty list if you don't want any of the greeks)
      self.greeksIncluded = ["Delta", "Gamma", "Vega", "Theta", "Rho", "Vomma", "Elasticity"]
      # self.greeksIncluded = []

      # Controls whether to track the details on each leg across the life of the trade (it generates a separate csv in the log with leg details at regular time intervals)
      self.trackLegDetails = False
      # The frequency (in minutes) with which the leg details are updated (used only if includeLegDetails = True). 
      # Updating with high frequency (i.e. every 5 minutes) will slow down the execution
      self.legDatailsUpdateFrequency = 15
	  
	  # The frequency (in minutes) with which each position is managed
      self.managePositionFrequency = 30

      # Controls whether to use the furthest (True) or the earliest (False) expiration date when multiple expirations are available in the chain
      self.useFurthestExpiry = True
      # Controls whether to consider the DTE of the last closed position when opening a new one:
      # If True, the Expiry date of the new position is selected such that the open DTE is the nearest to the DTE of the closed position
      self.dynamicDTESelection = False
      
	  # Minimum time distance between opening two consecutive trades
      self.minimumTradeScheduleDistance = timedelta(days = 1)


      # ########################################################################
      # Trading Strategies. 
      #   - Multiple strategies can be executed at the same time
      #   - Each strategy is processed indipendently of the others
      #   - New strategies can be created by extending the OptionStrategy class and implementing the getOrder method
      # Parameters details:
      #   - Net Delta: Used for Straddle, IronFly and Butterfly strategy. 
      #      - If netDelta = None        --> the Strategy will be centered around the ATM strike
      #      - If netDelta = n (-50, 50) --> the strike selection will be centered in a way to achieve the requested net delta exposure

      # ########################################################################
      
      # Holds all the strategies to be executed
      self.strategies = []
      
      # self.strategies.append(PutStrategy(self, delta = 10, creditStrategy = True))
      # self.strategies.append(CallStrategy(self, delta = 10, creditStrategy = True))
      # self.strategies.append(StraddleStrategy(self, name = "Straddle", netDelta = None, creditStrategy = True))
      # self.strategies.append(StrangleStrategy(self, name = "Strangle", putDelta = 10, callDelta = 10, creditStrategy = True))
      self.strategies.append(PutSpreadStrategy(self, name = "PS", delta = 10, wingSize = 25, creditStrategy = True))
      self.strategies.append(CallSpreadStrategy(self, name = "CS", delta = 10, wingSize = 25, creditStrategy = True))
      # self.strategies.append(IronCondorStrategy(self, name = "IC", putDelta = 10, callDelta = 10, putWingSize = 10, callWingSize = 10, creditStrategy = True))
      # self.strategies.append(IronFlyStrategy(self, name = "IF", netDelta = None, putWingSize = 10, callWingSize = 10, creditStrategy = True))
      # self.strategies.append(ButterflyStrategy(self, name = "Bfly", butteflyType = "Put", netDelta = None, butterflyLeftWingSize = 10, butterflyRightWingSize = 10, creditStrategy = True))
      # self.strategies.append(TEBombShelterStrategy(self, name = "TEBS", delta = 15, frontDte = self.dte - 30, hedgeAllocation = 0.1, chartUpdateFrequency = 5))
      # self.strategies.append(CustomStrategy(self
      #                                       , name = "BWB"
      #                                       , deltas = [50, 30, 10]
      #                                       , types = "Put"
      #                                       , sides = [1, -2, 1]
      #                                       , sidesDesc = ["Delta50Put", "Delta30Put", "Delta10Put"]
      #                                       , creditStrategy = None
      #                                       ))

      # Coarse filter for the Universe selection. It selects nStrikes on both sides of the ATM strike for each available expiration
      self.nStrikesLeft = 200
      self.nStrikesRight = 200

      # Time Resolution
      self.timeResolution = Resolution.Minute   # Resolution.Minute .Hour .Daily
      
      # Set brokerage model and margin account
      self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage, AccountType.Margin)

      # The start time at which the algorithm will start scheduling the strategy execution (to open new positions). No positions will be opened before this time
      self.scheduleStartTime = time(9, 45, 0)
      # Periodic interval with which the algorithm will check to open new positions
      self.scheduleFrequency = timedelta(hours = 1)
      
      # Setup the backtesting algorithm
      self.setupBacktest()
      
      # Setup the charts. Use the following flags to disable certain charts:
	  # PnL = False, Performance = False, WinLossStats = False, LossDetails = False
      self.setupCharts()
      
      
      
   def setupCharts(self, openPositions = True, Stats = True, PnL = True, WinLossStats = True, Performance = True, LossDetails = True):
      
      # Initialize flag (used to trigger a chart update)
      self.statsUpdated = False
      
      # Create an object to store all the stats
      self.stats = CustomObject()
      
      # Store the details about which charts will be plotted (there is a maximum of 10 series per backtest)
      self.stats.plot = CustomObject()
      self.stats.plot.openPositions = openPositions
      self.stats.plot.Stats = Stats
      self.stats.plot.PnL = PnL
      self.stats.plot.WinLossStats = WinLossStats
      self.stats.plot.Performance = Performance
      self.stats.plot.LossDetails = LossDetails
      
      # Initialize performance metrics
      self.stats.won = 0
      self.stats.lost = 0
      self.stats.winRate = 0.0
      self.stats.premiumCaptureRate = 0.0
      self.stats.totalCredit = 0.0
      self.stats.totalDebit = 0.0
      self.stats.PnL = 0.0
      self.stats.totalWinAmt = 0.0
      self.stats.totalLossAmt = 0.0
      self.stats.averageWinAmt = 0.0
      self.stats.averageLossAmt = 0.0
      self.stats.maxWin = 0.0
      self.stats.maxLoss = 0.0
      self.stats.testedCall = 0
      self.stats.testedPut = 0
      
      # Setup Charts
      if openPositions:
         activePositionsPlot = Chart('Open Positions')
         activePositionsPlot.AddSeries(Series('Open Positions', SeriesType.Line, ''))
      
      if Stats:
         statsPlot = Chart('Stats')
         statsPlot.AddSeries(Series('Won', SeriesType.Line, '', Color.Green))
         statsPlot.AddSeries(Series('Lost', SeriesType.Line, '', Color.Red))

      if PnL:
         pnlPlot = Chart('Profit and Loss')
         pnlPlot.AddSeries(Series('PnL', SeriesType.Line, ''))

      if WinLossStats:
         winLossStatsPlot = Chart('Win and Loss Stats')
         winLossStatsPlot.AddSeries(Series('Average Win', SeriesType.Line, '$', Color.Green))
         winLossStatsPlot.AddSeries(Series('Average Loss', SeriesType.Line, '$', Color.Red))

      if Performance:
         performancePlot = Chart('Performance')
         performancePlot.AddSeries(Series('Win Rate', SeriesType.Line, '%'))
         performancePlot.AddSeries(Series('Premium Capture', SeriesType.Line, '%'))

      # Loss Details chart. Only relevant in case of credit strategies
      if LossDetails:
         lossPlot = Chart('Loss Details')
         lossPlot.AddSeries(Series('Short Put Tested', SeriesType.Line, ''))
         lossPlot.AddSeries(Series('Short Call Tested', SeriesType.Line, ''))

      # Call the chart initialization method of each strategy (give a chance to setup custom charts)
      for strategy in self.strategies:
         strategy.setupCharts()

      # Add the first data point to the charts
      self.statsUpdated = True
      self.updateCharts()

   def updateCharts(self):

      # Start the timer
      self.executionTimer.start()

      # Call the updateCharts method of each strategy (give a chance to update any custom charts)
      for strategy in self.strategies:
         strategy.updateCharts()

      # Exit if there is nothing to update
      if not (self.statsUpdated or self.Time.time() == time(15, 59, 0)):
         return
   
      # Reset the flag
      self.statsUpdated = False
      
      plotInfo = self.stats.plot
      
      # Add the latest stats to the plots
      if plotInfo.openPositions:
         self.Plot("Open Positions", "Open Positions", self.currentActivePositions)
      if plotInfo.Stats:
         self.Plot("Stats", "Won", self.stats.won)
         self.Plot("Stats", "Lost", self.stats.lost)
      if plotInfo.PnL:
         self.Plot("Profit and Loss", "PnL", self.stats.PnL)
      if plotInfo.WinLossStats:
         self.Plot("Win and Loss Stats", "Average Win", self.stats.averageWinAmt)
         self.Plot("Win and Loss Stats", "Average Loss", self.stats.averageLossAmt)
      if plotInfo.Performance:
         self.Plot("Performance", "Win Rate", self.stats.winRate)
         self.Plot("Performance", "Premium Capture", self.stats.premiumCaptureRate)
      if plotInfo.LossDetails:
         self.Plot("Loss Details", "Short Put Tested", self.stats.testedPut)
         self.Plot("Loss Details", "Short Call Tested", self.stats.testedCall)
      
      # Stop the timer
      self.executionTimer.stop()


                  

   def setupBacktest(self):   
      
      # Set the logger
      self.logger = Logger(self, className = type(self).__name__, logLevel = self.logLevel)
      
      # Set the timer to monitor the execution performance
      self.executionTimer = Timer(self)
      
      # Number of currently active positions
      self.currentActivePositions = 0
      
      # Number of current working orders to open
      self.currentWorkingOrdersToOpen = 0
      
      # Initialize the dictionary to keep track of all positions
      self.allPositions = {}
      
      # Dictionary to keep track of all the available expiration dates at any given date
      self.expiryList = {}

      # Dictionary to keep track of all leg details across time
      self.positionTracking = {}
      
      # Add the underlying
      if self.ticker in ["SPX"]:
         # Underlying is SPX, trading SPXW
         underlying = self.AddIndex(self.ticker, self.timeResolution)
         option = self.AddIndexOption(underlying.Symbol, "SPXW", self.timeResolution)
      elif self.ticker in ["VIX"]:
         # Underlying is VIX
         underlying = self.AddIndex(self.ticker, self.timeResolution)
         option = self.AddIndexOption(underlying.Symbol, self.timeResolution)
      else:
         # Underlying is an equity
         underlying = self.AddEquity(self.ticker, self.timeResolution)
         option = self.AddOption(underlying.Symbol, self.timeResolution)
         
      # Set the benchmark.
      self.SetBenchmark(underlying.Symbol)


      # Store the symbol for the option and the underlying
      self.underlyingSymbol = underlying.Symbol
      self.optionSymbol = option.Symbol

      # Set data normalization mode to Raw
      underlying.SetDataNormalizationMode(DataNormalizationMode.Raw)

      # Keep track of the option contract subscriptions
      self.optionContractsSubscriptions = []

      # Set Security Initializer
      self.SetSecurityInitializer(self.securityInitializer)
      
      # Set the option chain filter function
      option.SetFilter(self.optionChainFilter)
            
      # -----------------------------------------------------------------------------
      # Scheduled functions (every xx minutes)
      # -----------------------------------------------------------------------------
      #self.Schedule.On(self.DateRules.EveryDay(self.underlyingSymbol)
      #                 , self.TimeRules.Every(TimeSpan.FromMinutes(self.scheduleFrequency))
      #                 , Action(self.openPosition)
      #                 )



   # Initialize the security every time that a new one is added
   def OnSecuritiesChanged(self, changes):
      for security in changes.AddedSecurities:
         self.securityInitializer(security)
      

   # Called every time a security (Option or Equity/Index) is initialized
   def securityInitializer(self, security):
      security.SetDataNormalizationMode(DataNormalizationMode.Raw)
      security.SetMarketPrice(self.GetLastKnownPrice(security))
      if security.Type in [SecurityType.Option, SecurityType.IndexOption]:
         security.SetFillModel(BetaFillModel(self))
         #security.SetFillModel(MidPriceFillModel(self))
         security.SetFeeModel(TastyWorksFeeModel())


   # Coarse filter for the option chain
   def optionChainFilter(self, universe):
      # Start the timer
      self.executionTimer.start()

      # Include Weekly contracts
      # nStrikes contracts to each side of the ATM
      # Contracts expiring in the range (DTE-5, DTE)
      filteredUniverse =  universe.IncludeWeeklys()\
                                  .Strikes(-self.nStrikesLeft, self.nStrikesRight)\
                                  .Expiration(max(0, self.dte - self.dteWindow), max(0, self.dte))

      # Stop the timer
      self.executionTimer.stop()
    
      return filteredUniverse

   
   def optionChainProviderFilter(self, symbols, min_strike_rank, max_strike_rank, minDte, maxDte):
      # Check if we got any symbols to process
      if len(symbols) == 0: 
         return None
         
      # Filter the symbols based on the expiry range
      filteredSymbols = [symbol for symbol in symbols 
                           if minDte <= (symbol.ID.Date.date() - self.Time.date()).days <= maxDte
                        ]

      # Exit if there are no symbols for the selected expiry range
      if not filteredSymbols: 
         return None

      # Get the latest price of the underlying
      underlyingLastPrice = self.Securities[self.underlyingSymbol].Price

      # Find the ATM strike
      atm_strike = sorted(filteredSymbols
                          ,key = lambda x: abs(x.ID.StrikePrice - self.Securities[self.underlyingSymbol].Price)
                          )[0].ID.StrikePrice
      
      # Get the list of available strikes
      strike_list = sorted(set([i.ID.StrikePrice for i in filteredSymbols]))
      
      # Find the index of ATM strike in the sorted strike list
      atm_strike_rank = strike_list.index(atm_strike)
      # Get the Min and Max strike price based on the specified number of strikes
      min_strike = strike_list[max(0, atm_strike_rank + min_strike_rank + 1)]
      max_strike = strike_list[min(atm_strike_rank + max_strike_rank - 1, len(strike_list)-1)]
            
      # Get the list of symbols within the selected strike range
      selectedSymbols = [symbol for symbol in filteredSymbols 
                              if min_strike <= symbol.ID.StrikePrice <= max_strike
                        ]

      # Loop through all Symbols and create a list of OptionContract objects
      contracts = []
      for symbol in selectedSymbols:
         # Create the OptionContract
         contract = OptionContract(symbol, symbol.Underlying)
         # Add this contract to the data subscription so we can retrieve the Bid/Ask price
         if not contract.Symbol in self.optionContractsSubscriptions:
            self.AddOptionContract(contract.Symbol, self.timeResolution)
            
         # Set the BidPrice
         contract.BidPrice = self.Securities[contract.Symbol].BidPrice
         # Set the AskPrice
         contract.AskPrice = self.Securities[contract.Symbol].AskPrice
         # Set the UnderlyingLastPrice
         contract.UnderlyingLastPrice = underlyingLastPrice
         # Add this contract to the output list
         contracts.append(contract)

      # Return the list of contracts
      return contracts   
   
   def getOptionContracts(self, slice):
      # Start the timer
      self.executionTimer.start()
      
      contracts = None
      # Set the DTE range (make sure values are not negative)
      minDte = max(0, self.dte - self.dteWindow)
      maxDte = max(0, self.dte)
      
      # Loop through all chains
      for chain in slice.OptionChains:
         # Look for the specified optionSymbol      
         if chain.Key != self.optionSymbol:
            continue  
         # Make sure there are any contracts in this chain   
         if chain.Value.Contracts.Count != 0:
            # Put the contracts into a list so we can cache the Greeks across multiple strategies
            contracts = [contract for contract in chain.Value
                              if minDte <= (contract.Expiry.date() - self.Time.date()).days <= maxDte
                        ]

      # If no chains were found, use OptionChainProvider to see if we can find any contracts
      # Only do this for short term expiration contracts (DTE < 3) where slice.OptionChains usually fails to retrieve any chains
      # We don't want to do this all the times for performance reasons
      if contracts == None and self.dte < 3:
         # Get the list of available option Symbols
         symbols = self.OptionChainProvider.GetOptionContractList(self.underlyingSymbol, self.Time)
         # Get the contracts
         contracts = self.optionChainProviderFilter(symbols, -self.nStrikesLeft, self.nStrikesRight, minDte, maxDte)

      # Stop the timer
      self.executionTimer.stop()
      
      return contracts

   def runStrategies(self):
      # Start the timer
      self.executionTimer.start()
      
      # Exit if the algorithm is warming up or the market is closed
      if self.IsWarmingUp or not self.IsMarketOpen(self.underlyingSymbol):
         return
      
      # Compute the schedule start datetime
      scheduleStartDttm = datetime.combine(self.Time.date(), self.scheduleStartTime)
      
      # Exit if we have not reached the the schedule start datetime
      if self.Time < scheduleStartDttm:
         return
         
      # Get the number of minutes since the schedule start time
      minutesSincescheduleStart = round((self.Time - scheduleStartDttm).seconds/60)
      
      # Convert the schedule frequency (timedelta) into a number of minutes
      scheduleFrequencyMinutes = round(self.scheduleFrequency.seconds/60)
      
      # Exit if we are not at the right scheduled interval
      if minutesSincescheduleStart % scheduleFrequencyMinutes != 0:
         return

      # Do not open any new positions if we have reached the maximum
      if (self.currentActivePositions + self.currentWorkingOrdersToOpen) >= self.maxActivePositions:
         return
      
      # Get the option chain
      chain = self.getOptionContracts(self.CurrentSlice)


      # Exit if we got no chains
      if chain == None:
         self.logger.debug(" -> No chains inside currentSlice!")
         return

      # The list of expiry dates will change once a day (at most). See if we have already processed this list for the current date
      if self.Time.date() in self.expiryList:
         # Get the expiryList from the dictionary
         expiryList = self.expiryList.get(self.Time.date())
      else:
         # Start the timer
         self.executionTimer.start(methodName = "runStrategies -> getExpiryList")
      
         # Set the DTE range (make sure values are not negative)
         minDte = max(0, self.dte - self.dteWindow)
         maxDte = max(0, self.dte)
         # Get the list of expiry dates, sorted in reverse order
         expiryList = sorted(set([contract.Expiry for contract in chain 
                                    if minDte <= (contract.Expiry.date() - self.Time.date()).days <= maxDte
                                  ]
                                 )
                             , reverse = True
                             )
         # Add the list to the dictionary
         self.expiryList[self.Time.date()] = expiryList
         # Log the list of expiration dates found in the chain
         self.logger.debug(f"Expiration dates in the chain: {len(expiryList)}")
         for expiry in expiryList:
            self.logger.debug(f" -> {expiry}")
         
         # Start the timer
         self.executionTimer.stop(methodName = "runStrategies -> getExpiryList")

      # Exit if we haven't found any Expiration cycles to process
      if not expiryList:
         return
      
      # Loop through all strategies
      for strategy in self.strategies:
         # Run the strategy
         strategy.run(chain, expiryList = expiryList)
      
      # Stop the timer
      self.executionTimer.stop()
   
   def OnOrderEvent(self, orderEvent):
      # Start the timer
      self.executionTimer.start()

      # Log the order event
      self.logger.debug(orderEvent)
   
      # Loop through all strategies
      for strategy in self.strategies:
         # Call the Strategy orderEvent handler
         strategy.handleOrderEvent(orderEvent)
         
      # Stop the timer
      self.executionTimer.stop()

   
   def OnData(self, slice):
      # Start the timer
      self.executionTimer.start()

      # Exit if the algorithm is warming up or the market is closed (avoid processing orders on the last minute as these will be executed the following day)
      if self.IsWarmingUp or not self.IsMarketOpen(self.underlyingSymbol) or self.Time.time() >= time(16, 0, 0):
         return
      
      # Update the charts
      self.updateCharts()

      # Exit if the algorithm is warming up
      if self.IsWarmingUp:
         return

      # Run the strategies to open new positions
      self.runStrategies()

      # Loop through all strategies
      for strategy in self.strategies:
         # Manage all the open positions for the current strategy
         strategy.managePositions()
      
      # Update the charts (in case any position was closed)
      self.updateCharts()

      # Stop the timer
      self.executionTimer.stop()


   def OnEndOfAlgorithm(self):
   
      # Convert the dictionary into a Pandas Data Frame
      dfAllPositions = pd.DataFrame.from_dict(self.allPositions, orient = "index")
   
      self.Log("")
      self.Log("---------------------------------")
      self.Log("     Execution  Statistics       ")
      self.Log("---------------------------------")
      self.executionTimer.showStats()
      self.Log("")
      self.Log("")
   
      self.Log("")
      self.Log("---------------------------------")
      self.Log("     Performance Statistics      ")
      self.Log("---------------------------------")
      self.Log("")
      self.Log(f"Total Contracts: {self.stats.won + self.stats.lost}")
      self.Log(f" -> Won: {self.stats.won}")
      self.Log(f" -> Lost: {self.stats.lost}")
      self.Log(f"    -> Win Rate: {self.stats.winRate}")
      self.Log(f"Total Credit: {self.stats.totalCredit}")
      self.Log(f"Total Debit: {self.stats.totalDebit}")
      self.Log(f"Total P&L: {self.stats.PnL}")
      self.Log(f"Average profit: {self.stats.averageWinAmt}")
      self.Log(f"Average Loss: {self.stats.averageLossAmt}")
      self.Log(f"Max Win: {self.stats.maxWin}")
      self.Log(f"Max Loss: {self.stats.maxLoss}")
      self.Log(f"Tested Calls: {self.stats.testedCall}")
      self.Log(f"Tested Puts: {self.stats.testedPut}")
      self.Log("")
      self.Log("")
      
      self.Log("---------------------------------")
      self.Log("           Trade Log             ")
      self.Log("---------------------------------")
      self.Log("")
      # Print the csv header
      self.Log(dfAllPositions.head(0).to_csv(index = False, header = True, line_terminator = " "))
      # Print the data frame to the log in csv format (one row at the time to avoid QC truncation limitation)
      for i in range(0, len(dfAllPositions.index)):
         self.Log(dfAllPositions.iloc[[i]].to_csv(index = False, header = False, line_terminator = " "))
      self.Log("")

      if self.positionTracking:
         first = True
         for position in self.positionTracking.values():
            if first:
               dfTracking = pd.DataFrame.from_dict(position, orient = "index")
               first = False
            else:
               dfTracking = dfTracking.append(pd.DataFrame.from_dict(position, orient = "index"), ignore_index = True)
         
         self.Log("---------------------------------")
         self.Log("           Leg Details           ")
         self.Log("---------------------------------")
         self.Log("")
         # Print the data frame to the log in csv format (one row at the time to avoid QC truncation limitation)
         self.Log(dfTracking.head(0).to_csv(index = False, header = True, line_terminator = " "))
         for i in range(0, len(dfTracking.index)):
            self.Log(dfTracking.iloc[[i]].to_csv(index = False, header = False, line_terminator = " "))
         self.Log("")

      
class TastyWorksFeeModel:
   def GetOrderFee(self, parameters):
      optionFee = min(10, parameters.Order.AbsoluteQuantity * 0.5)
      transactionFee = parameters.Order.AbsoluteQuantity * 0.14
      return OrderFee(CashAmount(optionFee + transactionFee, 'USD'))


# Dummy class useful to create empty objects
class CustomObject:
   pass


# Custom class: fills orders at the mid-price
class MidPriceFillModel(ImmediateFillModel):
   def __init__(self, context):
      self.context = context
      
   def MarketFill(self, asset, order):
      # Start the timer
      self.context.executionTimer.start()
   
      # Call the parent method
      fill = super().MarketFill(asset, order)
      # Compute the new fillPrice (at the mid-price)
      fillPrice = round(0.5*(asset.AskPrice + asset.BidPrice), 2)
      # Update the FillPrice attribute
      fill.FillPrice = fillPrice
      # Stop the timer
      self.context.executionTimer.stop()
      # Return the fill
      return fill

# Custom Fill model based on Beta distribution:
#  - Orders are filled based on a Beta distribution  skewed towards the mid-price with Sigma = bidAskSpread/6 (-> 99% fills within the bid-ask spread)
class BetaFillModel(ImmediateFillModel):

   # Initialize Random Number generator with a fixed seed (for replicability)
   random = np.random.RandomState(1234)
   
   def __init__(self, context):
      self.context = context
      
   def MarketFill(self, asset, order):
      # Start the timer
      self.context.executionTimer.start()
   
      # Get the random number generator
      random = BetaFillModel.random
      # Compute the Bid-Ask spread
      bidAskSpread = abs(asset.AskPrice - asset.BidPrice)
      # Compute the Mid-Price
      midPrice = 0.5*(asset.AskPrice + asset.BidPrice)
      # Call the parent method
      fill = super().MarketFill(asset, order)
      # Setting the parameters of the Beta distribution:
      # - The shape parameters (alpha and beta) are chosen such that the fill is "reasonably close" to the mid-price about 96% of the times
      # - How close -> The fill price is within 15% of half the bid-Ask spread
      if order.Direction == OrderDirection.Sell:
         # Beta distribution in the range [Bid-Price, Mid-Price], skewed towards the Mid-Price
         # - Fill price is within the range [Mid-Price - 0.15*bidAskSpread/2, Mid-Price] with about 96% probability
         offset = asset.BidPrice
         alpha = 20
         beta = 1
      else:
         # Beta distribution in the range [Mid-Price, Ask-Price], skewed towards the Mid-Price
         # - Fill price is within the range [Mid-Price, Mid-Price + 0.15*bidAskSpread/2] with about 96% probability
         offset = midPrice
         alpha = 1
         beta = 20
      # Range (width) of the Beta distribution
      range = bidAskSpread/2.0
      # Compute the new fillPrice (centered around the midPrice)
      fillPrice = round(offset + range * random.beta(alpha, beta), 2)
      # Update the FillPrice attribute
      fill.FillPrice = fillPrice
      # Stop the timer
      self.context.executionTimer.stop()
      # Return the fill
      return fill

      
class Timer:

   performanceTemplate = {"calls": 0.0
                          , "elapsedMin": float('Inf')
                          , "elapsedMean": None
                          , "elapsedMax": float('-Inf')
                          , "elapsedTotal": 0.0
                          , "elapsedLast": None
                          , "startTime": None
                          }
   
   def __init__(self, context):
      self.context = context
      self.performance = {}
      
   def start(self, methodName = None):
      # Get the name of the calling method
      methodName = methodName or sys._getframe(1).f_code.co_name
      # Get current performance stats
      performance = self.performance.get(methodName, Timer.performanceTemplate.copy())
      # Get the startTime
      performance["startTime"] = timer.perf_counter()
      # Save it back in the dictionary
      self.performance[methodName] = performance
      
      
   def stop(self, methodName = None):
      # Get the name of the calling method
      methodName = methodName or sys._getframe(1).f_code.co_name
      # Get current performance stats
      performance = self.performance.get(methodName)
      # Compute the elapsed
      elapsed = timer.perf_counter() - performance["startTime"]
      # Update the stats
      performance["calls"] += 1
      performance["elapsedLast"] = elapsed
      performance["elapsedMin"] = min(performance["elapsedMin"], elapsed)
      performance["elapsedMax"] = max(performance["elapsedMax"], elapsed)
      performance["elapsedTotal"] += elapsed
      performance["elapsedMean"] = performance["elapsedTotal"]/performance["calls"]
      
   def showStats(self, methodName = None):
      methods = methodName or self.performance.keys()
      for method in methods:
         performance = self.performance.get(method)
         if performance:
            self.context.logger.info(f"Execution Stats ({method}):")
            for key in performance:
               if key != "startTime":
                  if key == "calls" or performance[key] == None:
                     value = performance[key]
                  elif math.isinf(performance[key]):
                     value = None
                  else:
                     value = timedelta(seconds = performance[key])
                  self.context.logger.info(f"  --> {key}:{value}")
         else:
            self.context.logger.warning(f"There are no execution stats available for method {method}!")
