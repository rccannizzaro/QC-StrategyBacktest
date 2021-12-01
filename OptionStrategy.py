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

import re
import numpy as np
from Logger import *
from BSMLibrary import *
from StrategyBuilder import *

class OptionStrategy:

   # Internal counter for all the orders
   orderCount = 0

   # Default parameters
   defaultParameters = {
      "creditStrategy": True
      , "maxOrderQuantity": 1
      , "slippage": 0.0
      , "profitTarget": 0.6
      , "stopLossMultiplier": 1.5
      , "dte": 45
      , "dteThreshold": 21
      , "forceDteThreshold": False
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
   }

   @staticmethod
   def getNextOrderId():
      OptionStrategy.orderCount += 1
      return OptionStrategy.orderCount


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
      self.parameters = OptionStrategy.defaultParameters.copy()
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


   # Interface method. Must be implemented by the inheriting class
   def getOrder(self, chain):
      pass


   def run(self, chain, expiryList = None):
      if expiryList == None:
         # List of expiry dates, sorted in reverse order
         expiryList = sorted(set([contract.Expiry for contract in chain]), reverse = True)

      # Log the list of expiration dates found in the chain
      self.logger.trace("Expiration dates in the chain:")
      for expiry in expiryList:
         self.logger.trace(f" -> {expiry}")

      # Get the furthest expiry date
      expiry = expiryList[0]
      # Convert the date to a string
      expiryStr = expiry.strftime("%Y-%m-%d")

      # Proceed if we have not already opened a position on the given expiration
      if(expiryStr not in self.openPositions):
         # Filter the contracts in the chain, keep only the ones expiring on the given date
         filteredChain = self.filterByExpiry(chain, expiry = expiry, computeGreeks = True)
         # Call the getOrder method of the class implementing OptionStrategy 
         order = self.getOrder(filteredChain)
         # Execute the order
         self.openPosition(order)


   def filterByExpiry(self, chain, expiry = None, computeGreeks = True):
      # Check if the expiry date has been specified
      if expiry != None:
         # Filter contracts based on the requested expiry date
         filteredChain = [contract for contract in chain if contract.Expiry == expiry]
      else:
         # No filtering
         filteredChain = chain

      # Check if we need to compute the Greeks
      if computeGreeks:
         self.bsm.setGreeks(filteredChain)

      # Return the filtered contracts
      return filteredChain


   # Create dictionary with the details of the order to be submitted
   def getOrderDetails(self, contracts, sides, strategy, sell = True, strategyId = None):

      # Exit if there are no contracts to process
      if contracts == None or len(contracts) == 0:
         return

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters

      # Set the Strategy Id (if not specified)
      strategyId = strategyId or strategy.replace(" ", "")

      # Get the Expiration from the first contract
      expiry = contracts[0].Expiry
      # Dictionary: maps each contract to the the side (short/long) 
      contractSide = {}
      # Dictionary to keep track of all the strikes, Delta and IV
      strikes = {}
      deltas = {}
      IVs = {}

      # Compute the Mid-Price and Bid-Ask spread for the full order
      orderMidPrice = 0.0
      bidAskSpread = 0.0
      # Get the slippage parameter (if available)
      slippage = parameters["slippage"] or 0.0
      # Get the targetPremium
      targetPremium = parameters["targetPremium"] or 0.0
      # Get the limitOrderRelativePriceAdjustment
      limitOrderRelativePriceAdjustment = parameters["limitOrderRelativePriceAdjustment"] or 0.0
      # Get the limitOrderAbsolutePrice 
      limitOrderAbsolutePrice = parameters["limitOrderAbsolutePrice"]

      n = 0
      for contract in contracts:
         # Contract Side: +n -> Long, -n -> Short
         orderSide = sides[n]
         # Store it in the dictionary
         contractSide[contract.Symbol] = orderSide
         # Determine the prefix: Long/Short
         if sides[n] < 0:
            prefix = "short"
         else:
            prefix = "long"
         # Determine the type: Call/Put
         if contract.Right == OptionRight.Call:
            type = "Call"
         else:
            type = "Put"
         # Set the strike in the dictionary -> "<short|long><Call|Put>":<strike>
         strikes[f"{prefix}{type}"] = contract.Strike
         deltas[f"{prefix}{type}"] = contract.BSMGreeks.Delta
         IVs[f"{prefix}{type}"] = contract.BSMImpliedVolatility

         # Get the latest mid-price
         midPrice = self.midPrice(contract)
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
         
      if sell: # Credit order
         # Determine the order quantity based on the target premium
         orderQuantity = max(1, round(abs(targetPremium / (qtyMidPrice * 100))))
      else: # Debit order
         if parameters["targetPremium"] == None:
            # No target premium was provided. Use maxOrderQuantity
            orderQuantity = parameters["maxOrderQuantity"]
         # Get the maximum number of contracts not exceeding the target debit amount
         else:
            orderQuantity = math.floor(abs(targetPremium / (qtyMidPrice * 100)))

      # Create order details
      order = {"expiry": expiry
               , "expiryStr": expiry.strftime("%Y-%m-%d")
               , "strategyId": strategyId
               , "strategy": strategy
               , "contractSide": contractSide
               , "strikes": strikes
               , "deltas": deltas
               , "IVs": IVs
               , "contracts": contracts
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


   # Open a position based on the order details (as returned by getOrderDetails)
   def openPosition(self, order):

      # Exit if there is no order to process
      if order == None:
         return

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters

      # Get the list of contracts
      contracts = order["contracts"]
      # Exit if there are no contracts
      if(len(contracts) == 0):
         return

      useLimitOrders = parameters["useLimitOrders"]
      useMarketOrders = not useLimitOrders

      # Current timestamp
      currentDttm = self.context.Time

      # Extract order details. More readable than navigating the order dictionary..
      strategyId = order["strategyId"]
      contractSide = order["contractSide"]
      strikes = order["strikes"]
      deltas = order["deltas"]
      IVs = order["IVs"]
      expiry = order["expiry"]
      orderQuantity = order["orderQuantity"]
      bidAskSpread = order["open"]["bidAskSpread"]
      orderMidPrice = order["open"]["orderMidPrice"]
      limitOrderPrice = order["open"]["limitOrderPrice"]
      limitOrderAdjustment = order["open"]["limitOrderAdjustment"]
      slippage = order["open"]["slippage"]

      # Expiry String
      expiryStr = expiry.strftime("%Y-%m-%d")

      # Validate the order prior to submit
      if (  # We have a minimum order quantity
            orderQuantity == 0
            # The sign of orderMidPrice must be consistent with whether this is a credit strategy (+1) or debit strategy (-1)
            or np.sign(orderMidPrice) != 2*int(order["creditStrategy"]) - 1
            # Exit if the order quantity exceeds the maxOrderQuantity
            or (parameters["validateQuantity"] and orderQuantity > parameters["maxOrderQuantity"])
            # Make sure the bid-ask spread is not too wide before opening the position. 
            # Only for Market orders. In case of limit orders, this validation is done at the time of execution of the Limit order
            or (useMarketOrders and parameters["validateBidAskSpread"] and abs(bidAskSpread) > parameters["bidAskSpreadRatio"]*abs(orderMidPrice))
          ):
         return

      # Get the current price of the underlying
      underlyingPrice = contracts[0].UnderlyingLastPrice

      # Get the Order Id and add it to the order dictionary
      orderId = self.getNextOrderId()
      order["orderId"] = orderId
      # Create unique Tag to keep track of the order when the fill occurs
      orderTag = f"{strategyId}-{orderId}"
      order["orderTag"] = orderTag
      # Mark the time when this order has been submitted. This is needed to determine when to cancel Limit orders
      order["submittedDttm"] = currentDttm

      # Position dictionary. Used to keep track of the position and to report the results (will be converted into a flat csv)
      position = {"orderId"                 : orderId
                  , "expiryStr"             : expiryStr
                  , "openDttm"              : currentDttm
                  , "openDt"                : currentDttm.strftime("%Y-%m-%d")
                  , "openDTE"               : (expiry.date() - currentDttm.date()).days
                  , "closeDTE"              : float("NaN")
                  , "limitOrder"            : useLimitOrders
                  , "orderQuantity"         : orderQuantity
                  , "openOrderMidPrice"     : orderMidPrice
                  , "openOrderMidPrice.Min" : orderMidPrice
                  , "openOrderMidPrice.Max" : orderMidPrice
                  , "openOrderLimitPrice"   : limitOrderPrice
                  , "closeOrderMidPrice"    : 0.0
                  , "closeOrderMidPrice.Min": 0.0
                  , "closeOrderMidPrice.Max": 0.0
                  , "closeOrderLimitPrice"  : 0.0
                  , "openPremium"           : 0.0
                  , "closePremium"          : 0.0
                  , "P&L"                   : 0.0
                  , "P&L.Min"               : 0.0
                  , "P&L.Max"               : 0.0
                  , "underlyingPriceAtOrderOpen"   : underlyingPrice
                  , "underlyingPriceAtOpen"        : underlyingPrice
                  , "underlyingPriceAtOrderClose"  : float("NaN")
                  , "underlyingPriceAtClose"       : float("NaN")
                  , "openStalePrice"        : False
                  , "closeStalePrice"       : False
                  , "orderCancelled"        : False
                  }

      # Add details about strikes, Delta and IV of each contract in the order
      for key in strikes:
         position[f"{self.name}.{key}.Strike"] = strikes[key]
      for key in strikes:
         position[f"{self.name}.{key}.Delta"] = deltas[key]
      for key in strikes:
         position[f"{self.name}.{key}.IV"] = IVs[key]

      # Add this position to the global dictionary
      context.allPositions[orderId] = position
      # Add the details of this order to the openPositions dictionary.
      self.openPositions[expiryStr] = order

      # Keep track of all the working orders
      self.workingOrders[orderTag] = {}
      # Create the orders
      for contract in contracts:
         # Subscribe to the option contract data feed
         if not contract.Symbol in context.optionContractsSubscriptions:
            context.AddOptionContract(contract.Symbol, context.timeResolution)
            context.optionContractsSubscriptions.append(contract.Symbol)

         # Get the contract side (Long/Short)
         orderSide = contractSide[contract.Symbol]
         # Map each contract to the openPosition dictionary (key: expiryStr) 
         self.workingOrders[orderTag][contract.Symbol] = {"orderId": orderId
                                                          , "expiryStr" : expiryStr
                                                          , "orderType": "open"
                                                          , "fills": 0
                                                          }

         if useMarketOrders:
            # Send the Market order (asynchronous = True -> does not block the execution in case of partial fills)
            context.MarketOrder(contract.Symbol, orderSide * orderQuantity, asynchronous = True, tag = orderTag)
      ### Loop through all contracts   

      if useLimitOrders:
         # Keep track of all Limit orders
         self.limitOrders[orderTag] = {"orderId": orderId
                                       , "orderType": "open"
                                       , "contracts": contracts
                                       , "orderSides": [contractSide[contract.Symbol] for contract in contracts]
                                       , "orderQuantity": orderQuantity
                                       , "limitOrderPrice": limitOrderPrice
                                       }


   def manageLimitOrders(self):

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters

      # Get the slippage
      slippage = parameters["slippage"] or 0.0

      # Loop through all the Limit orders
      for orderTag in list(self.limitOrders):
         # Get the Limit order details
         limitOrder = self.limitOrders[orderTag]
         orderId = limitOrder["orderId"]
         position = context.allPositions[orderId]
         # Get the order type: open|close
         orderType = limitOrder["orderType"]
         # Get the contracts
         contracts = limitOrder["contracts"]
         # Get the order quantity
         orderQuantity = limitOrder["orderQuantity"]
         # Get the Limit price
         limitOrderPrice = limitOrder["limitOrderPrice"]

         # Sign of the order: open -> 1 (use orderSide as is),  close -> -1 (reverse the orderSide)
         orderSign = 2*int(orderType == "open")-1
         # Sign of the transaction: open -> -1,  close -> +1
         transactionSign = -orderSign
         # Get the mid price of each contract
         prices = np.array(list(map(self.latestMidPrice, contracts)))
         # Get the order sides
         orderSides = np.array(limitOrder["orderSides"])
         # Total slippage
         totalSlippage = sum(abs(orderSides)) * slippage
         # Compute the total order price (including slippage)
         midPrice = transactionSign * sum(orderSides * prices) - totalSlippage
         # Compute Bid-Ask spread
         bidAskSpread = sum(list(map(self.latestBidAskSpread, contracts)))
         # Keep track of the Limit order mid-price range
         position[f"{orderType}OrderMidPrice.Min"] = min(position[f"{orderType}OrderMidPrice.Min"], midPrice)
         position[f"{orderType}OrderMidPrice.Max"] = max(position[f"{orderType}OrderMidPrice.Max"], midPrice)

         
         if (# Check if we have reached the required price level
             midPrice >= limitOrderPrice
             # Validate the bid-ask spread to make sure it's not too wide
             and not (parameters["validateBidAskSpread"] and abs(bidAskSpread) > parameters["bidAskSpreadRatio"]*abs(midPrice))
             ):

            # Log the parameters used to validate the order
            self.logger.debug(f"Executing Limit Order to {orderType} the position:")
            self.logger.debug(f" - orderType: {orderType}")
            self.logger.debug(f" - orderQuantity: {orderQuantity}")
            self.logger.debug(f" - midPrice: {midPrice}  (limitOrderPrice: {limitOrderPrice})")
            self.logger.debug(f" - bidAskSpread: {bidAskSpread}")

            # Store the price of the underlying at the time of submitting the Market Order
            position[f"underlyingPriceAt{orderType.title()}"] = context.Securities[context.underlyingSymbol].Close
            # Initialize the counter
            n = 0
            for contract in contracts:
               # Set the order side: -1 -> Sell, +1 -> Buy
               orderSide = orderSign * orderSides[n]
               # Send the Market order (asynchronous = True -> does not block the execution in case of partial fills)
               context.MarketOrder(contract.Symbol, orderSide * orderQuantity, asynchronous = True, tag = orderTag)
               # Increment the counter
               n += 1
            ### for contract in contracts

            # Remove the order from the self.limitOrders dictionary
            self.limitOrders.pop(orderTag)


   def OnOrderEvent(self, orderEvent):

      # Log the order event
      self.logger.debug(orderEvent)

      # Get the context
      context = self.context

      # Process only Fill events 
      if not (orderEvent.Status == OrderStatus.Filled or orderEvent.Status == OrderStatus.PartiallyFilled):
         return

      if(orderEvent.IsAssignment):
         # TODO: Liquidate the assigned position. 
         #  Eventually figure out which open position it belongs to and close that position.
         pass

      # Get the orderEvent id
      orderEventId = orderEvent.OrderId
      # Retrieve the order associated to this events
      order = context.Transactions.GetOrderById(orderEventId)
      # Get the order tag. Remove any warning text that might have been added in case of Fills at Stale Price
      orderTag = re.sub(" - Warning.*", "", order.Tag)

      # Exit if this order tag is not in the list of open orders. (Check for orderTag = None -> assignments have no order tag)
      if orderTag == None or orderTag not in self.workingOrders:
         return

      contractInfo = self.workingOrders[orderTag][orderEvent.Symbol]
      # Get the order id and expiryStr value for the contract
      orderId = contractInfo["orderId"]
      expiryStr = contractInfo["expiryStr"]
      orderType = contractInfo["orderType"]

      # Exit if this expiry date is not in the list of open positions
      if expiryStr not in self.openPositions:
         return

      # Retrieve the open position
      openPosition = self.openPositions[expiryStr]
      # Get the quantity used to open the position
      positionQuantity = openPosition["orderQuantity"]
      # Get the side of each leg (-n -> Short, +n -> Long)
      contractSides = openPosition["contractSide"].values()
      # Total number of legs in the position
      Nlegs = sum(list(map(abs,contractSides)))

      # Check if the contract was filled at a stale price (Warnings in the orderTag)
      if re.search(" - Warning.*", order.Tag):
         self.logger.warning(order.Tag)
         openPosition[orderType]["stalePrice"] = True
         context.allPositions[orderId][f"{orderType}StalePrice"] = True

      # Add the order to the list of openPositions orders (only if this is the first time the order is filled  - in case of partial fills)
      if contractInfo["fills"] == 0:
         openPosition[orderType]["orders"].append(order)

      # Update the number of filled contracts associated with this order
      contractInfo["fills"] += abs(orderEvent.FillQuantity)

      # Remove this order entry from the self.workingOrders[orderTag] dictionary if it has been fully filled
      if contractInfo["fills"] == positionQuantity:
         removedOrder = self.workingOrders[orderTag].pop(orderEvent.Symbol)

      # Update the counter of positions that have been filled
      openPosition[orderType]["fills"] += abs(orderEvent.FillQuantity)
      # Get the total amount of the transaction
      transactionAmt = orderEvent.FillQuantity * orderEvent.FillPrice * 100
      # Check if this is a fill order for an entry position
      if orderType == "open":
         # Update the openPremium field to include the current transaction (use "-=" to reverse the side of the transaction: Short -> credit, Long -> debit)
         context.allPositions[orderId]["openPremium"] -= transactionAmt
      else: # This is an order for the exit position
         # Update the closePremium field to include the current transaction  (use "-=" to reverse the side of the transaction: Sell -> credit, Buy -> debit)
         context.allPositions[orderId]["closePremium"] -= transactionAmt

      # Check if all legs have been filled
      if openPosition[orderType]["fills"] == Nlegs*positionQuantity:
         openPosition[orderType]["filled"] = True
         # Set the time when the full order was filled
         context.allPositions[orderId][orderType + "FilledDttm"] = context.Time
         # Record the order mid price
         context.allPositions[orderId][orderType + "OrderMidPrice"] = openPosition[orderType]["orderMidPrice"]
         if orderType == "open":
            # Trigger an update of the charts
            context.statsUpdated = True
            # Increment the counter of active positions
            context.currentActivePositions += 1
            # Store the credit received (needed to determine the stop loss): value is per share (divided by 100)
            openPosition[orderType]["premium"] = context.allPositions[orderId]["openPremium"] / 100

      # Check if the entire position has been closed
      if orderType == "close" and openPosition["open"]["filled"] and openPosition["close"]["filled"]:

         # Compute P&L for the position
         positionPnL = context.allPositions[orderId]["openPremium"] + context.allPositions[orderId]["closePremium"]

         # Store the PnL for the position
         context.allPositions[orderId]["P&L"] = positionPnL
         # Now we can remove the position from the self.openPositions dictionary
         removedPosition = self.openPositions.pop(expiryStr)
         # Decrement the counter of active positions
         context.currentActivePositions -= 1

         # ###########################
         # Collect Performance metrics
         # ###########################
         self.updateStats(removedPosition)


   def updateStats(self, closedPosition):

      # Get the context
      context = self.context

      orderId = closedPosition["orderId"]
      # Get the position P&L
      positionPnL = context.allPositions[orderId]["P&L"]
      # Get the price of the underlying at the time of closing the position
      priceAtClose = context.allPositions[orderId]["underlyingPriceAtClose"]

      if closedPosition["creditStrategy"]:
         # Update total credit (the position was opened for a credit)
         context.stats.totalCredit += context.allPositions[orderId]["openPremium"]
         # Update total debit (the position was closed for a debit)
         context.stats.totalDebit += context.allPositions[orderId]["closePremium"]
      else:
         # Update total credit (the position was closed for a credit)
         context.stats.totalCredit += context.allPositions[orderId]["closePremium"]
         # Update total debit (the position was opened for a debit)
         context.stats.totalDebit += context.allPositions[orderId]["openPremium"]

      # Update the total P&L
      context.stats.PnL += positionPnL
      # Update Win/Loss counters
      if positionPnL > 0:
         context.stats.won += 1
         context.stats.totalWinAmt += positionPnL
         context.stats.maxWin = max(context.stats.maxWin, positionPnL)
         context.stats.averageWinAmt = context.stats.totalWinAmt / context.stats.won
      else:
         context.stats.lost += 1
         context.stats.totalLossAmt += positionPnL
         context.stats.maxLoss = min(context.stats.maxLoss, positionPnL)
         context.stats.averageLossAmt = -context.stats.totalLossAmt / context.stats.lost

         # Check if this is a Credit Strategy
         if closedPosition["creditStrategy"]:
            strikes = closedPosition["strikes"]
            shortPutStrike = 0
            shortCallStrike = float('inf')
            # Get the short strikes (if any)
            if("shortPut" in strikes):
               shortPutStrike = strikes["shortPut"]
            if("shortCall" in strikes):
               shortCallStrike = strikes["shortCall"]

            # Check if the short Put is in the money
            if priceAtClose <= shortPutStrike:
               context.stats.testedPut += 1
            # Check if the short Call is in the money
            elif priceAtClose >= shortCallStrike:
               context.stats.testedCall += 1
            # Check if the short Put is being tested
            elif (priceAtClose-shortPutStrike) < (shortCallStrike - priceAtClose):
               context.stats.testedPut += 1
            # The short Call is being tested
            else:
               context.stats.testedCall += 1

      # Update the Win Rate
      if ((context.stats.won + context.stats.lost) > 0):
         context.stats.winRate = 100*context.stats.won/(context.stats.won + context.stats.lost)

      if context.stats.totalCredit > 0:
         context.stats.premiumCaptureRate = 100*context.stats.PnL/context.stats.totalCredit

      # Trigger an update of the charts
      context.statsUpdated = True


   def getPositionValue(self, position):

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters

      # Initialize result dictionary
      positionDetails = {"orderParameters":[]}

      # Get the amount of credit received to open the position
      openPremium = position["open"]["premium"]
      orderQuantity = position["orderQuantity"]

      # Loop through all legs of the open position
      orderMidPrice = 0.0
      limitOrderPrice = 0.0
      bidAskSpread = 0.0
      for contract in position["contracts"]:
         # Reverse the original contract side
         orderSide = -position["contractSide"][contract.Symbol]
         # Compute the Bid-Ask spread
         bidAskSpread += self.bidAskSpread(context.Securities[contract.Symbol])
         # Get the latest mid-price
         midPrice = self.midPrice(context.Securities[contract.Symbol])
         # Adjusted mid-price (including slippage)
         adjustedMidPrice = midPrice + orderSide * parameters["slippage"]
         # Total order mid-price
         orderMidPrice -= orderSide * midPrice
         # Total Limit order mid-price (including slippage)
         limitOrderPrice -= orderSide * adjustedMidPrice
         # Add the parameters needed to place a Market/Limit order if needed
         positionDetails["orderParameters"].append(
               {"symbol": contract.Symbol
                , "orderSide": orderSide
                , "orderQuantity": orderQuantity
                , "limitPrice": adjustedMidPrice
               }
            )

      # Check if the mid-price is positive: avoid closing the position if the Bid-Ask spread is too wide (more than 25% of the credit received)
      positionPnL = openPremium + orderMidPrice*orderQuantity
      if self.parameters["validateBidAskSpread"] and bidAskSpread > parameters["bidAskSpreadRatio"]*openPremium:
         self.logger.trace(f"The Bid-Ask spread is too wide. Open Premium: {openPremium},  Mid-Price: {orderMidPrice},  Bid-Ask Spread: {bidAskSpread}")
         positionPnL = None

      # Set Order Id and expiration
      positionDetails["orderId"] = position["orderId"]
      positionDetails["expiryStr"] = position["expiryStr"]
      # Set the order tag
      positionDetails["orderTag"] = position["orderTag"]
      # Store the full mid-price of the position
      positionDetails["orderMidPrice"] = orderMidPrice
      # Store the Limit Order mid-price of the position (including slippage)
      positionDetails["limitOrderPrice"] = limitOrderPrice
      # Store the full bid-ask spead of the position
      positionDetails["bidAskSpread"] = bidAskSpread
      # Store the position PnL
      positionDetails["positionPnL"] = positionPnL

      return positionDetails


   def closePosition(self, positionDetails, stopLossFlg = False):

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters

      # Get Order Id and expiration
      orderId = positionDetails["orderId"]
      expiryStr = positionDetails["expiryStr"]
      orderTag = positionDetails["orderTag"]
      orderMidPrice = positionDetails["orderMidPrice"]
      limitOrderPrice = positionDetails["limitOrderPrice"]
      bidAskSpread = positionDetails["bidAskSpread"]

      # Get the details currently open position 
      openPosition = self.openPositions[expiryStr]
      # Extract the expiry date
      expiry = openPosition["expiry"]
      # Get the contracts and their side
      contracts = openPosition["contracts"]
      contractSide = openPosition["contractSide"]
      # Set the expiration threshold at 15:40 of the expiration date (but no later than the market close cut-off time).
      expirationThreshold = min(expiry + timedelta(hours = 15, minutes = 40), datetime.combine(expiry, parameters["marketCloseCutoffTime"]))
      # Set the expiration date for the Limit order. Make sure it does not exceed the expiration threshold
      limitOrderExpiryDttm = min(context.Time + parameters["limitOrderExpiration"], expirationThreshold)

      # Determine if we are going to use a Limit Order
      useLimitOrders = (# Check if we are suposed to use Limit orders as a default
                        parameters["useLimitOrders"]
                        # Make sure there is enough time left to expiration. 
                        # Once we cross the expiration threshold (10 minutes from market close on the expiration day) we are going to submit a Market order
                        and context.Time <= expirationThreshold
                        # It's not a stop loss (stop losses are executed through a Market order)
                        and not stopLossFlg
                        )      
      # Determine if we are going to use a Market Order
      useMarketOrders = not useLimitOrders

      # Get the price of the underlying at the time of closing the position
      priceAtClose = None
      if context.Securities.ContainsKey(context.underlyingSymbol):
         if context.Securities[context.underlyingSymbol] != None:
            priceAtClose = context.Securities[context.underlyingSymbol].Close
         else:
            self.logger.warning("priceAtClose is None")

      # Set the midPrice for the order to close
      openPosition["close"]["orderMidPrice"] = orderMidPrice
      # Set the Limit order expiration. 
      openPosition["close"]["limitOrderExpiryDttm"] = limitOrderExpiryDttm

      # Set the timestamp when the closing order is created
      context.allPositions[orderId]["closeDttm"] = context.Time
      # Set the date when the closing order is created
      context.allPositions[orderId]["closeDt"] = context.Time.strftime("%Y-%m-%d")
      # Set the price of the underlying at the time of submitting the order to close
      context.allPositions[orderId]["underlyingPriceAtOrderClose"] = priceAtClose
      # Set the price of the underlying at the time of submitting the order to close:
      # - This is the same as underlyingPriceAtOrderClose in case of Market Orders
      # - In case of Limit orders, this is the actual price of the underlying at the time when the Limit Order was triggered (price is updated later by the manageLimitOrders method)      
      context.allPositions[orderId]["underlyingPriceAtClose"] = priceAtClose
      # Set the mid-price of the position at the time of closing
      context.allPositions[orderId]["closeOrderMidPrice"] = orderMidPrice
      context.allPositions[orderId]["closeOrderMidPrice.Min"] = orderMidPrice
      context.allPositions[orderId]["closeOrderMidPrice.Max"] = orderMidPrice
      # Set the Limit Order price of the position at the time of closing
      context.allPositions[orderId]["closeOrderLimitPrice"] = limitOrderPrice
      # Set the close DTE
      context.allPositions[orderId]["closeDTE"] = (self.openPositions[expiryStr]["expiry"].date() - context.Time.date()).days

      if useMarketOrders:
         # Log the parameters used to validate the order
         self.logger.debug("Executing Market Order to close the position:")
         self.logger.debug(f" - orderQuantity: {openPosition['orderQuantity']}")
         self.logger.debug(f" - midPrice: {orderMidPrice}")
         self.logger.debug(f" - bidAskSpread: {bidAskSpread}")

      # Submit the close orders
      self.workingOrders[orderTag] = {}
      for orderParameters in positionDetails["orderParameters"]:
         # Extract order parameters
         symbol = orderParameters["symbol"]
         orderSide = orderParameters["orderSide"]
         orderQuantity = orderParameters["orderQuantity"]
         limitPrice = orderParameters["limitPrice"]
         # Map each contract to the openPosition dictionary (-> expiryStr) 
         self.workingOrders[orderTag][symbol] = {"orderId": orderId, "expiryStr" : expiryStr, "orderType": "close", "fills": 0}

         # Determine what type of order (Limit/Market) should be executed.
         if useMarketOrders:
            # Send the Market order (asynchronous = True -> does not block the execution in case of partial fills)
            context.MarketOrder(symbol, orderSide * orderQuantity, asynchronous = True, tag = orderTag)
      ### Loop through all contracts   

      if useLimitOrders:
         # Keep track of all Limit orders
         self.limitOrders[orderTag] = {"orderId": orderId
                                       , "orderType": "close"
                                       , "contracts": contracts
                                       , "orderSides": [contractSide[contract.Symbol] for contract in contracts]
                                       , "orderQuantity": openPosition["orderQuantity"]
                                       , "limitOrderPrice": limitOrderPrice
                                       }


   def managePositions(self):

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters

      # Manage any Limit orders that have not been executed
      self.manageLimitOrders()

      # Loop through all open positions
      for expiryStr in list(self.openPositions):
         # Skip this contract if in the meantime it has been removed by the onOrderEvent
         if expiryStr not in self.openPositions:
            continue

         # Get the position
         position = self.openPositions[expiryStr]
         # Get the order tag
         orderTag = position["orderTag"]
         # How many days to expiration are left for this position
         currentDte = (position["expiry"].date() - context.Time.date()).days

         # Get the order id
         orderId = position["orderId"]

         # Check if this is a fully filled position
         if position["open"]["filled"] == True:

            # Check if we have any pending working orders to close
            if len(self.workingOrders[orderTag]) > 0:

               # Check if we have a partial fill
               if position["close"]["fills"] > 0:
                  # This shouldn't really happen since Limit orders are executed through Market orders
                  self.logger.trace(f"Close order {position['orderTag']} has a partial fill.")
               else: # No fills at all
                  # Check if we need to cancel the order
                  if context.Time > position["close"]["limitOrderExpiryDttm"]:
                     # Remove the order from the self.limitOrders dictionary (make sure this order has not been removed in the meantime by the earlier call to manageLimitOrders)
                     if orderTag in self.limitOrders:
                        self.limitOrders.pop(orderTag)
                     # Reset the order from the self.workingOrders dictionary
                     self.workingOrders[orderTag] = {}
                  ### if context.Time > position["close"]["limitOrderExpiryDttm"]
               ### No fills at all
            else: # There are no orders to close
               # Get the amount of credit received to open the position
               openPremium = position["open"]["premium"]
               # Get the quantity used to open the position
               positionQuantity = position["orderQuantity"]

               # Possible Scenarios:
               #   - Credit Strategy: 
               #        -> openPremium > 0
               #        -> profitTarget <= 1
               #        -> stopLossMultiplier >= 1
               #        -> maxLoss = Depending on the strategy 
               #   - Debit Strategy:
               #        -> openPremium < 0
               #        -> profitTarget >= 0
               #        -> stopLossMultiplier <= 1
               #        -> maxLoss = openPremium

               # Set the target profit amount:
               targetProfit = abs(openPremium) * parameters["profitTarget"]
               # Set the stop loss amount
               stopLoss = -abs(openPremium) * parameters["stopLossMultiplier"]
               # Maximum Loss (pre-computed at the time of creating the order)
               maxLoss = position["maxLoss"] * positionQuantity
               # Add the premium to compute the net loss
               netMaxLoss = maxLoss + openPremium

               # Get the current value of the position
               positionDetails = self.getPositionValue(position)
               # Extract the positionPnL (per share)
               positionPnL = positionDetails["positionPnL"]

               # Exit if the positionPnL is not available (bid-ask spread is too wide)
               if positionPnL == None:
                  return

               # Check if we've hit the stop loss threshold
               stopLossFlg = netMaxLoss <= positionPnL <= stopLoss

               # Keep track of the P&L range throughout the life of the position
               context.allPositions[orderId]["P&L.Min"] = min(context.allPositions[orderId]["P&L.Min"], 100*positionPnL)
               context.allPositions[orderId]["P&L.Max"] = max(context.allPositions[orderId]["P&L.Max"], 100*positionPnL)

               # Check if we need to close the position
               if (positionPnL >= targetProfit # We hit the profit target
                   or stopLossFlg # We hit the stop loss (making sure we don't exceed the max loss in case of spreads)
                   or (parameters["dteThreshold"] != None # The dteThreshold has been specified
                       and parameters["dte"] > parameters["dteThreshold"] # We are using the dteThreshold only if the open DTE was larger than the threshold
                       and currentDte <= parameters["dteThreshold"] # We have reached the DTE threshold
                       and (parameters["forceDteThreshold"] == True # The position must be closed when reaching the threshold (hard stop)
                            or positionPnL >= 0 # Soft stop: close as soon as it is profitable
                            ) 
                       )
                   or (currentDte == 0 and context.Time.time() > parameters["marketCloseCutoffTime"]) # The option expires today and we are 5 minutes away from market close
                   ):
                  # Close the position
                  self.closePosition(positionDetails, stopLossFlg = stopLossFlg)

         else: # The open position has not been fully filled (this must be a Limit order)
            # Check if we have a partial fill
            if position["open"]["fills"] > 0:
               # This shouldn't really happen since Limit orders are executed through Market orders
               self.logger.trace(f"Open order {position['orderTag']} has a partial fill.")
            else: # No fills at all
               # Check if we need to cancel the order
               if context.Time > position["open"]["limitOrderExpiryDttm"]:
                  # Remove the order from the self.limitOrders dictionary
                  if orderTag in self.limitOrders:
                     self.limitOrders.pop(orderTag)
                  # Remove this position from the list of open positions
                  if expiryStr in self.openPositions:
                     self.openPositions.pop(expiryStr)
                  # Reset the order from the self.workingOrders dictionary
                  self.workingOrders[orderTag] = {}
                  # Mark the order as being cancelled
                  context.allPositions[orderId]["orderCancelled"] = True
                  # Remove the cancelled position from the final output unless we are required to include it
                  if not parameters["includeCancelledOrders"]:
                     context.allPositions.pop(orderId)
               ### if context.Time > position["open"]["limitOrderExpiryDttm"]
            ### No fills at all
         ### The open position has not been fully filled (this must be a Limit order)


   # Returns the latest mid-price of an option contract
   def latestMidPrice(self, contract):
      return self.midPrice(self.context.Securities[contract.Symbol])

   def latestBidAskSpread(self, contract):
      return self.bidAskSpread(self.context.Securities[contract.Symbol])
      
   # Returns the mid-price of an option contract
   def midPrice(self, contract):
      return 0.5*(contract.BidPrice + contract.AskPrice)


   # Returns the mid-price of an option contract
   def bidAskSpread(self, contract):
      return abs(contract.AskPrice - contract.BidPrice)


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


   def getNakedOrder(self, contracts, type, strike = None, delta = None, sell = True):
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
         sorted_contracts = self.strategyBuilder.getPuts(contracts, toDelta = delta, toStrike = strike)
      elif type == "call":
         # Get all Calls with a strike higher than the given strike and delta lower than the given delta
         sorted_contracts = self.strategyBuilder.getCalls(contracts, toDelta = delta, fromStrike = strike)
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
      order = self.getOrderDetails(legs, sides, strategy, sell)
      # Return the order
      return order
