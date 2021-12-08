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
########################################################################################

import numpy as np
from Logger import *
from BSMLibrary import *
from StrategyBuilder import *

class OptionStrategyOrder:

   # Internal counter for all the orders
   orderCount = 0

   # Default parameters
   defaultParameters = {
      "creditStrategy": True
      , "maxOrderQuantity": 1
      , "slippage": 0.0
      , "profitTarget": 0.6
      , "stopLossMultiplier": 1.5
      # If multiple expirations are available in the chain, should we use the furthest (True) or the earliest (False)
      , "useFurthestExpiry": True
      # Controls whether to consider the DTE of the last closed position when opening a new one:
      # If True, the Expiry date of the new position is selected such that the open DTE is the nearest to the DTE of the closed position
      , "dynamicDTESelection": False
      , "dte": 45
      , "dteWindow": 7
      , "dteThreshold": 21
      , "forceDteThreshold": False
      # Credit Targeting: either using a fixed credit amount (targetPremium) or a dynamic credit (percentage of Net Liquidity)
      , "targetPremiumPct": None
      , "targetPremium": None
      # Limit Order Management
      , "useLimitOrders": True
      , "limitOrderRelativePriceAdjustment": 0
      , "limitOrderAbsolutePrice": None
      , "limitOrderExpiration": timedelta(hours = 8)
      # Delta and Wing size used for Naked Put/Call and Spreads
      , "delta": 10
      , "wingSize": 10
      # Put/Call delta for Iron Condor
      , "putDelta": 10
      , "callDelta": 10
      # Net delta for Straddle, Iron Fly and Butterfly (using ATM strike if netDelta = None)
      , "netDelta": None
      # Put/Call Wing size for Iron Condor, Iron Fly
      , "putWingSize": 10
      , "callWingSize": 10
      # Butterfly specific parameters
      , "butteflyType": None
      , "butterflyLeftWingSize": 10
      , "butterflyRightWingSize": 10
      # If True, the order is submitted as long as it does not exceed the maxOrderQuantity.
      , "validateQuantity": True
      # If True, the order mid-price is validated to make sure the Bid-Ask spread is not too wide.
      , "validateBidAskSpread": False
      # Used when validateBidAskSpread = True. if the ratio between the bid-ask spread and the mid-price is higher than this parameter, the order is not executed
      , "bidAskSpreadRatio": 0.3
      # The time (on expiration day) at which any position that is still open will closed
      , "marketCloseCutoffTime": time(15, 45, 0)
      # Controls whether to include Cancelled orders (Limit orders that didn't fill) in the final output
      , "includeCancelledOrders": True
      # Controls whether to include details on each leg (open/close fill price and descriptive statistics about mid-price, Greeks, and IV)
      , "includeLegDetails": False
      # Control whether to allow multiple positions to be opened for the same Expiration date
      , "allowMultipleEntriesPerExpiry": False
      # The frequency (in minutes) with which the leg details are updated (used only if includeLegDetails = True)
      , "legDatailsUpdateFrequency": 30
      # Controls the memory (in minutes) of EMA process. The exponential decay is computed such that the contribution of each value decays by 95% after <emaMemory> minutes (i.e. decay^emaMemory = 0.05)
      , "emaMemory": 200
   }

   @staticmethod
   def getNextOrderId():
      OptionStrategyOrder.orderCount += 1
      return OptionStrategyOrder.orderCount


   # \param[in] context is a reference to the QCAlgorithm instance. The following attributes are used from the context:
   #    - slippage: (Optional) controls how the mid-price of an order is adjusted to include slippage.
   #    - targetPremium: (Optional) used to determine how many contracts to buy/sell.  
   #    - maxOrderQuantity: (Optional) Caps the number of contracts that are bought/sold (Default: 1). 
   #         If targetPremium == None  -> This is the number of contracts bought/sold.
   #         If targetPremium != None  -> The order is executed only if the number of contracts required to reach the target credit/debit does not exceed the maxOrderQuantity
   def __init__(self, context, name = None, **kwargs):
      # Set the context (QCAlgorithm object)
      self.context = context
      # Set default name (use the class name) if no value has been provided 
      name = name or type(self).__name__
      # Set the Strategy Name
      self.name = name
      # Set the logger
      self.logger = Logger(context, className = type(self).__name__, logLevel = context.logLevel)
      # Initialize the BSM pricing model
      self.bsm = BSM(context)
      # Initialize the Strategy Builder
      self.strategyBuilder = StrategyBuilder(context)

      # Initialize the parameters dictionary with the default values
      self.parameters = OptionStrategyOrder.defaultParameters.copy()
      # Override default parameters with values that might have been set in the context
      for key in self.parameters:
         if hasattr(context, key):
            self.parameters[key] = getattr(context, key)
      # Now merge the dictionary with any kwargs parameters that might have been specified directly with the constructor (kwargs takes precedence)
      self.parameters.update(kwargs)

      # Create dictionary to keep track of all the open positions related to this strategy
      self.openPositions = {}
      # Create dictionary to keep track of all the working orders
      self.workingOrders = {}
      # Create dictionary to keep track of all the limit orders
      self.limitOrders = {}
      # Create FIFO list to keep track of all the recently closed positions (needed for the Dynamic DTE selection)
      self.recentlyClosedDTE = []


   # Interface method. Must be implemented by the inheriting class
   def setupCharts(self):
      pass
      
   # Interface method. Must be implemented by the inheriting class
   def updateCharts(self):
      pass
      
   # Interface method. Must be implemented by the inheriting class
   def getOrder(self, chain):
      pass
      
   # Returns the mid-price of an option contract
   def midPrice(self, contract):
      security = self.context.Securities[contract.Symbol]
      return 0.5*(security.BidPrice + security.AskPrice)

   # Returns the mid-price of an option contract
   def bidAskSpread(self, contract):
      security = self.context.Securities[contract.Symbol]
      return abs(security.AskPrice - security.BidPrice)

   def getMaxOrderQuantity(self):
      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters
      
      # Get the maximum order quantity parameter
      maxOrderQuantity = parameters["maxOrderQuantity"]
      # Get the targetPremiumPct
      targetPremiumPct = parameters["targetPremiumPct"]
      # Check if we are using dynamic premium targeting
      if targetPremiumPct != None:
         # Scale the maxOrderQuantity consistently with the portfolio growth
         maxOrderQuantity = round(maxOrderQuantity * (1 + context.Portfolio.TotalProfit / context.initialAccountValue))
         # Make sure we don't go below the initial parameter value
         maxOrderQuantity = max(parameters["maxOrderQuantity"], maxOrderQuantity)
      # Return the result   
      return maxOrderQuantity


   def lastTradingDay(self, expiry):
      # Get the trading calendar
      tradingCalendar = self.context.TradingCalendar
      # Find the last trading day for the given expiration date
      lastDay = list(tradingCalendar.GetDaysByType(TradingDayType.BusinessDay, expiry - timedelta(days = 20), expiry))[-1].Date
      return lastDay

   # Create dictionary with the details of the order to be submitted
   def getOrderDetails(self, contracts, sides, strategy, sell = True, strategyId = None, expiry = None, sidesDesc = None):

      # Exit if there are no contracts to process
      if contracts == None or len(contracts) == 0:
         return

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters

      # Set the Strategy Id (if not specified)
      strategyId = strategyId or strategy.replace(" ", "")

      # Get the Expiration from the first contract (unless otherwise specified
      expiry = expiry or contracts[0].Expiry
      # Get the last trading day for the given expiration date (in case it falls on a holiday)
      expiryLastTradingDay = self.lastTradingDay(expiry)
      # Set the date/time threshold by which the position must be closed (on the last trading day before expiration)
      expiryMarketCloseCutoffDttm = datetime.combine(expiryLastTradingDay, parameters["marketCloseCutoffTime"])
      # Dictionary to map each contract symbol to the side (short/long) 
      contractSide = {}
      # Dictionary to map each contract symbol to its decription 
      contractSideDesc = {}
      # Dictionary to map each contract symbol to the actual contract object
      contractDictionary = {}
      
      # Dictionaries to keep track of all the strikes, Delta and IV
      strikes = {}
      delta = {}
      gamma = {}
      vega = {}
      theta = {}
      rho = {}
      vomma = {}
      elasticity = {}
      IV = {}
      midPrices = {}

      # Compute the Mid-Price and Bid-Ask spread for the full order
      orderMidPrice = 0.0
      bidAskSpread = 0.0
      # Get the slippage parameter (if available)
      slippage = parameters["slippage"] or 0.0
         
      # Get the limitOrderRelativePriceAdjustment
      limitOrderRelativePriceAdjustment = parameters["limitOrderRelativePriceAdjustment"] or 0.0
      # Get the limitOrderAbsolutePrice 
      limitOrderAbsolutePrice = parameters["limitOrderAbsolutePrice"]


      # Get the maximum order quantity
      maxOrderQuantity = self.getMaxOrderQuantity()
      # Get the targetPremiumPct
      targetPremiumPct = parameters["targetPremiumPct"]
      # Check if we are using dynamic premium targeting
      if targetPremiumPct != None:
         # Make sure targetPremiumPct is bounded to the range [0, 1])
         targetPremiumPct = max(0.0, min(1.0, targetPremiumPct))
         # Compute the target premium as a percentage of the total net portfolio value
         targetPremium = context.Portfolio.TotalPortfolioValue * targetPremiumPct
      else:
         targetPremium = parameters["targetPremium"]

      # Check if we have a description for the contracts
      if sidesDesc == None:
         # Temporary dictionaries to lookup a description
         optionTypeDesc = {OptionRight.Put: "Put", OptionRight.Call: "Call"}
         optionSideDesc = {-1: "short", 1: "long"}
         # create a description for each contract: <long|short><Call|Put>
         sidesDesc = list(map(lambda contract, side: f"{optionSideDesc[np.sign(side)]}{optionTypeDesc[contract.Right]}", contracts, sides))

      n = 0
      for contract in contracts:
         # Contract Side: +n -> Long, -n -> Short
         orderSide = sides[n]
         # Contract description (<long|short><Call|Put>)
         orderSideDesc = sidesDesc[n]
         
         # Store it in the dictionary
         contractSide[contract.Symbol] = orderSide
         contractSideDesc[contract.Symbol] = orderSideDesc
         contractDictionary[contract.Symbol] = contract

         # Set the strike in the dictionary -> "<short|long><Call|Put>": <strike>
         strikes[f"{orderSideDesc}"] = contract.Strike
         # Set the Greeks and IV in the dictionary -> "<short|long><Call|Put>": <greek|IV>
         delta[f"{orderSideDesc}"] = contract.BSMGreeks.Delta
         gamma[f"{orderSideDesc}"] = contract.BSMGreeks.Gamma
         vega[f"{orderSideDesc}"] = contract.BSMGreeks.Vega
         theta[f"{orderSideDesc}"] = contract.BSMGreeks.Theta
         rho[f"{orderSideDesc}"] = contract.BSMGreeks.Rho
         vomma[f"{orderSideDesc}"] = contract.BSMGreeks.Vomma
         elasticity[f"{orderSideDesc}"] = contract.BSMGreeks.Elasticity
         IV[f"{orderSideDesc}"] = contract.BSMImpliedVolatility

         # Get the latest mid-price
         midPrice = self.midPrice(contract)
         # Store the midPrice in the dictionary -> "<short|long><Call|Put>": midPrice
         midPrices[f"{orderSideDesc}"] = midPrice
         # Compute the bid-ask spread
         bidAskSpread += self.bidAskSpread(contract)         
         # Adjusted mid-price (include slippage). Take the sign of orderSide to determine the direction of the adjustment
         adjustedMidPrice = midPrice + np.sign(orderSide) * slippage
         # Keep track of the total credit/debit or the order
         orderMidPrice -= orderSide * midPrice

         # Increment counter
         n += 1

      # Exit if the order mid-price is zero
      if abs(orderMidPrice) < 1e-5:
         return
         
      # Compute Limit Order price
      if limitOrderAbsolutePrice != None:
         # Compute the relative price adjustment (needed to adjust each leg with the same proportion)
         limitOrderRelativePriceAdjustment = limitOrderAbsolutePrice / orderMidPrice - 1
         # Use the specified absolute price
         limitOrderPrice = limitOrderAbsolutePrice
      else:
         # Set the Limit Order price (including slippage)
         limitOrderPrice = orderMidPrice * (1 + limitOrderRelativePriceAdjustment)

      # Compute the total slippage
      totalSlippage = sum(list(map(abs, sides))) * slippage
      # Add slippage to the limit order
      limitOrderPrice -= totalSlippage

      # Round the prices to the nearest cent
      orderMidPrice = round(orderMidPrice, 2)
      limitOrderPrice = round(limitOrderPrice, 2)

      # Determine which price is used to compute the order quantity
      if parameters["useLimitOrders"]:
         # Use the Limit Order price
         qtyMidPrice = limitOrderPrice
      else:
         # Use the contract mid-price
         qtyMidPrice = orderMidPrice

      # Exit if the price is zero
      if abs(qtyMidPrice) <= 1e-5:
         return
      

      if targetPremium == None:
         # No target premium was provided. Use maxOrderQuantity
         orderQuantity = maxOrderQuantity
      else:   
         # Make sure we are not exceeding the available portfolio margin
         targetPremium = min(context.Portfolio.MarginRemaining, targetPremium)

         # Determine the order quantity based on the target premium
         orderQuantity = abs(targetPremium / (qtyMidPrice * 100))
         
         # Different logic for Credit vs Debit strategies
         if sell: # Credit order
            # Sell at least one contract
            orderQuantity = max(1, round(orderQuantity))
         else: # Debit order
            # make sure the total price does not exceed the target premium
            orderQuantity = math.floor(orderQuantity)

      # Create order details
      order = {"expiry": expiry
               , "expiryStr": expiry.strftime("%Y-%m-%d")
               , "expiryLastTradingDay": expiryLastTradingDay
               , "expiryMarketCloseCutoffDttm": expiryMarketCloseCutoffDttm
               , "strategyId": strategyId
               , "strategy": strategy
               , "sides": sides
               , "sidesDesc": sidesDesc
               , "contractSide": contractSide
               , "contractSideDesc": contractSideDesc
               , "contractDictionary": contractDictionary
               , "strikes": strikes
               , "midPrices": midPrices
               , "delta": delta
               , "gamma": gamma
               , "vega": vega
               , "theta": theta
               , "rho": rho
               , "vomma": vomma
               , "elasticity": elasticity
               , "IV": IV
               , "contracts": contracts
               , "targetPremium": targetPremium
               , "maxOrderQuantity": maxOrderQuantity
               , "orderQuantity": orderQuantity
               , "creditStrategy": sell
               , "maxLoss": self.computeOrderMaxLoss(contracts, sides)
               , "open": {"orders": []
                          , "fills": 0
                          , "filled": False
                          , "stalePrice": False
                          , "limitOrderAdjustment": limitOrderRelativePriceAdjustment
                          , "orderMidPrice": orderMidPrice
                          , "limitOrderPrice": limitOrderPrice
                          , "qtyMidPrice": qtyMidPrice
                          , "limitOrder": parameters["useLimitOrders"]
                          , "limitOrderExpiryDttm": context.Time + parameters["limitOrderExpiration"]
                          , "slippage": slippage
                          , "totalSlippage": totalSlippage
                          , "bidAskSpread": bidAskSpread
                          , "fillPrice": 0.0
                          }
               , "close": {"orders": []
                           , "fills": 0
                           , "filled": False
                           , "stalePrice": False
                           , "orderMidPrice": 0.0
                           , "fillPrice": 0.0
                           }
            }

      return order


   def getPayoff(self, spotPrice, contracts, sides):
      # Exit if there are no contracts to process
      if len(contracts) == 0:
         return 0

      # Initialize the counter
      n = 0
      # initialize the payoff
      payoff = 0
      for contract in contracts:
         # direction: Call -> +1, Put -> -1
         direction = 2*int(contract.Right == OptionRight.Call)-1
         # Add the payoff of the current contract
         payoff += sides[n] * max(0, direction * (spotPrice - contract.Strike))
         # Increment the counter
         n += 1

      # Return the payoff
      return payoff
      
   def computeOrderMaxLoss(self, contracts, sides):
      # Exit if there are no contracts to process
      if len(contracts) == 0:
         return 0

      # Get the current price of the underlying
      UnderlyingLastPrice = contracts[0].UnderlyingLastPrice
      # Evaluate the payoff at the extreme (spotPrice = 0)
      maxLoss = self.getPayoff(0, contracts, sides)
      # Evaluate the payoff at each strike
      for contract in contracts:
         maxLoss = min(maxLoss, self.getPayoff(contract.Strike, contracts, sides))

      # Evaluate the payoff at the extreme (spotPrice = 10x higher)
      maxLoss = min(maxLoss, self.getPayoff(UnderlyingLastPrice*10, contracts, sides))      
      # Cap the payoff at zero: we are only interested in losses
      maxLoss = min(0, maxLoss)
      # Return the max loss
      return maxLoss


   def getNakedOrder(self, contracts, type, strike = None, delta = None, fromPrice = None, toPrice = None, sell = True):
      if sell:
         # Short option contract
         sides = [-1]
         strategy = f"Short {type.title()}"
      else:
         # Long option contract
         sides = [1]
         strategy = f"Long {type.title()}"

      type = type.lower()
      if type == "put":
         # Get all Puts with a strike lower than the given strike and delta lower than the given delta
         sorted_contracts = self.strategyBuilder.getPuts(contracts, toDelta = delta, toStrike = strike, fromPrice = fromPrice, toPrice = toPrice)
      elif type == "call":
         # Get all Calls with a strike higher than the given strike and delta lower than the given delta
         sorted_contracts = self.strategyBuilder.getCalls(contracts, toDelta = delta, fromStrike = strike, fromPrice = fromPrice, toPrice = toPrice)
      else:
         self.logger.error(f"Input parameter type = {type} is invalid. Valid values: Put|Call.")
         return

      # Check if we got any contracts
      if len(sorted_contracts):
         # Create order details
         order = self.getOrderDetails([sorted_contracts[0]], sides, strategy, sell)
         # Return the order
         return order


   # Create order details for a Straddle order
   def getStraddleOrder(self, contracts, strike = None, netDelta = None, sell = True):

      if sell:
         # Short Straddle
         sides = [-1, -1]
      else:
         # Long Straddle
         sides = [1, 1]

      # Delta strike selection (in case the Iron Fly is not centered on the ATM strike)
      delta = None
      # Make sure the netDelta is less than 50 
      if netDelta != None and abs(netDelta) < 50:
         delta = 50 + netDelta 

      if strike == None and delta == None:
         # Standard Straddle: get the ATM contracts
         legs = self.strategyBuilder.getATM(contracts)
      else:
         legs = []
         # This is a Straddle centered at the given strike or Net Delta.          
         # Get the Put at the requested delta or strike
         puts = self.strategyBuilder.getPuts(contracts, toDelta = delta, toStrike = strike)
         if(len(puts) > 0):
            put = puts[0]

            # Get the Call at the same strike as the Put
            calls = self.strategyBuilder.getCalls(contracts, fromStrike = put.Strike)
            if(len(calls) > 0):
               call = calls[0]
               # Collect both legs
               legs = [put, call]

      # Create order details
      order = self.getOrderDetails(legs, sides, "Straddle", sell)
      # Return the order
      return order


   # Create order details for a Strangle order
   def getStrangleOrder(self, contracts, callDelta = None, putDelta = None, callStrike = None, putStrike = None, sell = True):

      if sell:
         # Short Strangle
         sides = [-1, -1]
      else:
         # Long Strangle
         sides = [1, 1]

      # Get all Puts with a strike lower than the given putStrike and delta lower than the given putDelta
      puts = self.strategyBuilder.getPuts(contracts, toDelta = putDelta, toStrike = putStrike)
      # Get all Calls with a strike higher than the given callStrike and delta lower than the given callDelta
      calls = self.strategyBuilder.getCalls(contracts, toDelta = callDelta, fromStrike = callStrike)

      # Get the two contracts
      legs = []
      if len(puts) > 0 and len(calls) > 0:
         legs = [puts[0], calls[0]]

      # Create order details
      order = self.getOrderDetails(legs, sides, "Strangle", sell)
      # Return the order
      return order


   def getSpreadOrder(self, contracts, type, strike = None, delta = None, wingSize = None, sell = True):

      if sell:
         # Credit Spread
         sides = [-1, 1]
         strategy = f"{type.title()} Credit Spread"
      else:
         # Debit Spread
         sides = [1, -1]
         strategy = f"{type.title()} Debit Spread"

      # Get the legs of the spread
      legs = self.strategyBuilder.getSpread(contracts, type, strike = strike, delta = delta, wingSize = wingSize)

      # Exit if we couldn't get both legs of the spread
      if len(legs) != 2:
         return

      # Create order details
      order = self.getOrderDetails(legs, sides, strategy, sell)
      # Return the order
      return order


   def getIronCondorOrder(self, contracts, callDelta = None, putDelta = None, callStrike = None, putStrike = None, callWingSize = None, putWingSize = None, sell = True):

      if sell:
         # Sell Iron Condor: [longPut, shortPut, shortCall, longCall]
         sides = [1, -1, -1, 1]
         strategy = "Iron Condor"
      else:
         # Buy Iron Condor: [shortPut, longPut, longCall, shortCall]
         sides = [-1, 1, 1, -1]
         strategy = "Reverse Iron Condor"

      # Get the Put spread
      puts = self.strategyBuilder.getSpread(contracts, "Put", strike = putStrike, delta = putDelta, wingSize = putWingSize, sortByStrike = True)
      # Get the Call spread
      calls = self.strategyBuilder.getSpread(contracts, "Call", strike = callStrike, delta = callDelta, wingSize = callWingSize)

      # Collect all legs
      legs = puts + calls

      # Exit if we couldn't get all legs of the Iron Condor
      if len(legs) != 4:
         return

      # Create order details
      order = self.getOrderDetails(legs, sides, strategy, sell)
      # Return the order
      return order


   def getIronFlyOrder(self, contracts, netDelta = None, strike = None, callWingSize = None, putWingSize = None, sell = True):

      if sell:
         # Sell Iron Fly: [longPut, shortPut, shortCall, longCall]
         sides = [1, -1, -1, 1]
         strategy = "Iron Fly"
      else:
         # Buy Iron Fly: [shortPut, longPut, longCall, shortCall]
         sides = [-1, 1, 1, -1]
         strategy = "Reverse Iron Fly"

      # Delta strike selection (in case the Iron Fly is not centered on the ATM strike)
      delta = None
      # Make sure the netDelta is less than 50 
      if netDelta != None and abs(netDelta) < 50:
         delta = 50 + netDelta 

      if strike == None and delta == None:
         # Standard ATM Iron Fly
         strike = self.strategyBuilder.getATMStrike(contracts)

      # Get the Put spread
      puts = self.strategyBuilder.getSpread(contracts, "Put", strike = strike, delta = delta, wingSize = putWingSize, sortByStrike = True)      
      # Get the Call spread with the same strike as the first leg of the Put spread
      calls = self.strategyBuilder.getSpread(contracts, "Call", strike = puts[0].Strike, wingSize = callWingSize)

      # Collect all legs
      legs = puts + calls

      # Exit if we couldn't get all legs of the Iron Fly
      if len(legs) != 4:
         return

      # Create order details
      order = self.getOrderDetails(legs, sides, strategy, sell)
      # Return the order
      return order


   def getButterflyOrder(self, contracts, type, netDelta = None, strike = None, leftWingSize = None, rightWingSize = None, sell = False):

      # Make sure the wing sizes are set
      leftWingSize = leftWingSize or rightWingSize or 1
      rightWingSize = rightWingSize or leftWingSize or 1

      if sell:
         # Sell Butterfly: [short<Put|Call>, 2 long<Put|Call>, short<Put|Call>]
         sides = [-1, 2, -1]
         strategy = "Credit Butterfly"
      else:
         # Buy Butterfly: [long<Put|Call>, 2 short<Put|Call>, long<Put|Call>]
         sides = [1, -2, 1]
         strategy = "Debit Butterfly"

      # Create a custom description for each side to uniquely identify the wings:
      # Sell Butterfly: [leftShort<Put|Call>, 2 Long<Put|Call>, rightShort<Put|Call>]
      # Buy Butterfly: [leftLong<Put|Call>, 2 Short<Put|Call>, rightLong<Put|Call>]
      optionSides = {-1: "Short", 1: "Long"}
      sidesDesc = list(map(lambda side, prefix: f"{prefix}{optionSides[np.sign(side)]}{type.title()}", sides, ["left", "", "right"]))
      
      
      # Delta strike selection (in case the Butterfly is not centered on the ATM strike)
      delta = None
      # Make sure the netDelta is less than 50 
      if netDelta != None and abs(netDelta) < 50:
         if type.lower() == "put":
            # Use Put delta
            delta = 50 + netDelta
         else:
            # Use Call delta
            delta = 50 - netDelta

      if strike == None and delta == None:
         # Standard ATM Butterfly
         strike = self.strategyBuilder.getATMStrike(contracts)

      type = type.lower()
      if type == "put":
         # Get the Put spread (sorted by strike in ascending order)
         putSpread = self.strategyBuilder.getSpread(contracts, "Put", strike = strike, delta = delta, wingSize = leftWingSize, sortByStrike = True)
         # Exit if we couldn't get all legs of the Iron Fly
         if len(putSpread) != 2:
            return
         # Get the middle strike (second entry in the list)
         middleStrike = putSpread[1].Strike
         # Find the right wing of the Butterfly (add a small offset to the fromStrike in order to avoid selecting the middle strike as a wing)
         wings = self.strategyBuilder.getPuts(contracts, fromStrike = middleStrike + 0.1, toStrike = middleStrike + rightWingSize)
         # Exit if we could not find the wing
         if len(wings) == 0:
            return
         # Combine all the legs
         legs = putSpread + wings[0]
      elif type == "call":
         # Get the Call spread (sorted by strike in ascending order)
         callSpread = self.strategyBuilder.getSpread(contracts, "Call", strike = strike, delta = delta, wingSize = rightWingSize)
         # Exit if we couldn't get all legs of the Iron Fly
         if len(callSpread) != 2:
            return
         # Get the middle strike (first entry in the list)
         middleStrike = callSpread[0].Strike
         # Find the left wing of the Butterfly (add a small offset to the toStrike in order to avoid selecting the middle strike as a wing)
         wings = self.strategyBuilder.getCalls(contracts, fromStrike = middleStrike - leftWingSize, toStrike = middleStrike - 0.1)
         # Exit if we could not find the wing
         if len(wings) == 0:
            return
         # Combine all the legs
         legs = wings[0] + callSpread
      else:
         self.logger.error(f"Input parameter type = {type} is invalid. Valid values: Put|Call.")
         return

      # Exit if we couldn't get both legs of the spread
      if len(legs) != 3:
         return

      # Create order details
      order = self.getOrderDetails(legs, sides, strategy, sell = sell, sidesDesc = sidesDesc)
      # Return the order
      return order
