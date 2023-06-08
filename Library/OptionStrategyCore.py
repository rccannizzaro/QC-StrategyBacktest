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

import re
import numpy as np
from Logger import *
from OptionStrategyOrder import *

class OptionStrategyCore(OptionStrategyOrder):

   def run(self, chain, expiryList = None):
      # Start the timer
      self.context.executionTimer.start()

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters
      
      # DTE range
      dte = parameters["dte"]
      dteWindow = parameters["dteWindow"]

      # Controls whether to select the furtest or the earliest expiry date
      useFurthestExpiry = parameters["useFurthestExpiry"]
      # Controls whether to enable dynamic selection of the expiry date
      dynamicDTESelection = parameters["dynamicDTESelection"]      
      # Controls whether to allow mutiple entries for the same expiry date
      allowMultipleEntriesPerExpiry = parameters["allowMultipleEntriesPerExpiry"]

      # Set the DTE range (make sure values are not negative)
      minDte = max(0, dte - dteWindow)
      maxDte = max(0, dte)

      # Get the maximum number of active positions that are allowed for this strategy
      maxActivePositions = parameters.get("maxActivePositions")
      
      # Exit if we are already at full capacity
      if (maxActivePositions != None
          and (self.currentActivePositions + self.currentWorkingOrdersToOpen) >= maxActivePositions
          ):
         return

      # Get the minimum time distance between consecutive trades
      minimumTradeScheduleDistance = parameters.get("minimumTradeScheduleDistance", timedelta(hours = 0))
      # Make sure the minimum required amount of time has passed since the last trade was opened
      if (self.lastOpenedDttm != None 
          and context.Time < (self.lastOpenedDttm + minimumTradeScheduleDistance)
          ):
         return
         
      # Check if the epiryList was specified as an input
      if expiryList == None or dte != context.dte or dteWindow != context.dteWindow:
         # List of expiry dates, sorted in reverse order
         expiryList = sorted(set([contract.Expiry for contract in chain
                                    if minDte <= (contract.Expiry.date() - context.Time.date()).days <= maxDte
                                  ]
                                 )
                             , reverse = True
                             )
         # Log the list of expiration dates found in the chain
         self.logger.debug(f"Expiration dates in the chain: {len(expiryList)}")  
         for expiry in expiryList:
            self.logger.debug(f" -> {expiry}")

      # Exit if we haven't found any Expiration cycles to process
      if not expiryList:
         # Stop the timer
         self.context.executionTimer.stop()
         return
         
      
      # Get the DTE of the last closed position
      lastClosedDte = None
      lastClosedOrderTag = None
      if self.recentlyClosedDTE:
         while(self.recentlyClosedDTE):
            # Pop the oldest entry in the list (FIFO)
            lastClosedTradeInfo = self.recentlyClosedDTE.pop(0)
            if lastClosedTradeInfo["closeDte"] >= minDte:
               lastClosedDte = lastClosedTradeInfo["closeDte"]
               lastClosedOrderTag = lastClosedTradeInfo["orderTag"]
               # We got a good entry, get out of the loop
               break

      # Check if we need to do dynamic DTE selection
      if dynamicDTESelection and lastClosedDte != None:
         # Get the expiration with the nearest DTE as that of the last closed position
         expiry = sorted(expiryList
                         , key = lambda expiry: abs((expiry.date() - context.Time.date()).days - lastClosedDte)
                         , reverse = False
                         )[0]
      else:
         # Determine the index used to select the expiry date:
         # useFurthestExpiry = True -> expiryListIndex = 0 (takes the first entry -> furthest expiry date since the expiry list is sorted in reverse order)
         # useFurthestExpiry = False -> expiryListIndex = -1 (takes the last entry -> earliest expiry date since the expiry list is sorted in reverse order)
         expiryListIndex = int(useFurthestExpiry) - 1
         # Get the expiry date
         expiry = expiryList[expiryListIndex]
         
         
      # Convert the date to a string
      expiryStr = expiry.strftime("%Y-%m-%d")
      
      # Proceed if we have not already opened a position on the given expiration (unless we are allowed to open multiple positions on the same expiry date)
      if(parameters["allowMultipleEntriesPerExpiry"] or expiryStr not in self.openPositions):
         # Filter the contracts in the chain, keep only the ones expiring on the given date
         filteredChain = self.filterByExpiry(chain, expiry = expiry)
         # Call the getOrder method of the class implementing OptionStrategy 
         order = self.getOrder(filteredChain)
         # Execute the order
         self.openPosition(order, linkedOrderTag = lastClosedOrderTag)

      # Stop the timer
      self.context.executionTimer.stop()

   def filterByExpiry(self, chain, expiry = None, computeGreeks = False):
      # Start the timer
      self.context.executionTimer.start()
      
      # Check if the expiry date has been specified
      if expiry != None:
         # Filter contracts based on the requested expiry date
         filteredChain = [contract for contract in chain if contract.Expiry == expiry]
      else:
         # No filtering
         filteredChain = chain

      # Check if we need to compute the Greeks for every single contract (this is expensive!)
      # By defauls, the Greeks are only calculated while searching for the strike with the requested delta, so there should be no need to set computeGreeks = True
      if computeGreeks:
         self.bsm.setGreeks(filteredChain)

      # Stop the timer
      self.context.executionTimer.stop()

      # Return the filtered contracts
      return filteredChain

   # Open a position based on the order details (as returned by getOrderDetails)
   def openPosition(self, order, linkedOrderTag = None):

      # Exit if there is no order to process
      if order == None:
         return

      # Start the timer
      self.context.executionTimer.start()

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters

      # Track the details of each leg? Only if includeLegDetails = True
      trackLegDetails = parameters["trackLegDetails"] and parameters["includeLegDetails"]
      
      # Get the list of contracts
      contracts = order["contracts"]
      # Exit if there are no contracts
      if(len(contracts) == 0):
         return

      useLimitOrders = parameters["useLimitOrders"]
      useMarketOrders = not useLimitOrders
      
      # Get the EMA memory
      emaMemory = parameters["emaMemory"]

      # Current timestamp
      currentDttm = self.context.Time

      # Extract order details. More readable than navigating the order dictionary..
      strategyId = order["strategyId"]
      contractSide = order["contractSide"]
      sides = order["sides"]
      sidesDesc = order["sidesDesc"]
      midPrices = order["midPrices"]
      strikes = order["strikes"]
      IVs = order["IV"]
      expiry = order["expiry"]
      targetPremium = order["targetPremium"]
      maxOrderQuantity = order["maxOrderQuantity"]
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
            or (parameters["validateQuantity"] and orderQuantity > maxOrderQuantity)
            # Make sure the bid-ask spread is not too wide before opening the position. 
            # Only for Market orders. In case of limit orders, this validation is done at the time of execution of the Limit order
            or (useMarketOrders and parameters["validateBidAskSpread"] and abs(bidAskSpread) > parameters["bidAskSpreadRatio"]*abs(orderMidPrice))
          ):
         return

      # Get the current price of the underlying
      underlyingPrice = self.contractUtils.getUnderlyingLastPrice(contracts[0])

      # Get the Order Id and add it to the order dictionary
      orderId = self.getNextOrderId()
      order["orderId"] = orderId
      # Create unique Tag to keep track of the order when the fill occurs
      orderTag = f"{strategyId}-{orderId}"
      order["orderTag"] = orderTag
      # Mark the time when this order has been submitted. This is needed to determine when to cancel Limit orders
      order["submittedDttm"] = currentDttm
      
      if parameters["allowMultipleEntriesPerExpiry"]:
         positionKey = orderId
      else:
         positionKey = expiryStr

      # Dictionary to keep track of the details of each leg across time
      if trackLegDetails:
         context.positionTracking[orderId] = {}
         # Initialize the first record
         positionTracking = {"orderId": orderId
                             , "Time": currentDttm
                             }

      # Position dictionary. Used to keep track of the position and to report the results (will be converted into a flat csv)
      position = {"orderId"                 : orderId
                  , "orderTag"              : orderTag
                  , "Strategy"              : self.name
                  , "StrategyTag"           : self.nameTag
                  , "expiryStr"             : expiryStr
                  , "linkedOrderTag"        : linkedOrderTag
                  , "openDttm"              : currentDttm
                  , "openDt"                : currentDttm.strftime("%Y-%m-%d")
                  , "openDTE"               : (expiry.date() - currentDttm.date()).days
                  , "closeDTE"              : float("NaN")
                  , "DIT"                   : float("NaN")
                  , "closeReason"           : ""
                  , "limitOrder"            : useLimitOrders
                  , "targetPremium"         : targetPremium
                  , "orderQuantity"         : orderQuantity
                  , "maxOrderQuantity"      : maxOrderQuantity
                  , "openOrderMidPrice"     : orderMidPrice
                  , "openOrderMidPrice.Min" : orderMidPrice
                  , "openOrderMidPrice.Max" : orderMidPrice
                  , "openOrderBidAskSpread" : bidAskSpread
                  , "openOrderLimitPrice"   : limitOrderPrice
                  , "closeOrderMidPrice"    : 0.0
                  , "closeOrderMidPrice.Min": 0.0
                  , "closeOrderMidPrice.Max": 0.0
                  , "closeOrderBidAskSpread": float("NaN")
                  , "closeOrderLimitPrice"  : 0.0
                  , "openPremium"           : 0.0
                  , "closePremium"          : 0.0
                  , "P&L"                   : 0.0
                  , "P&L.Min"               : 0.0
                  , "P&L.Max"               : 0.0
                  , "P&L.Min.DIT"           : 0.0
                  , "P&L.Max.DIT"           : 0.0
                  , "underlyingPriceAtOrderOpen"   : underlyingPrice
                  , "underlyingPriceAtOpen"        : underlyingPrice
                  , "underlyingPriceAtOrderClose"  : float("NaN")
                  , "underlyingPriceAtClose"       : float("NaN")
                  , "openStalePrice"        : False
                  , "closeStalePrice"       : False
                  , "orderCancelled"        : False
                  , "statsUpdateCount"      : 1.0
                  , "irReferenceDate"       : self.bsm.irDate
                  , "riskFreeRate"          : self.bsm.riskFreeRate
                  }

      # Using separate loops here so that the final CSV file has the columns in the desired order
      # Add details about strikes of each contract in the order
      for key in sidesDesc:
         position[f"{self.name}.{key}.Strike"] = strikes[key]

      # Add details about the mid price, fill price and related stats 
      for key, side in zip(sidesDesc, sides):
         position[f"{self.name}.{key}.Expiry"] = order["contractExpiry"][key].strftime("%Y-%m-%d")
         position[f"{self.name}.{key}.side"] = side
         position[f"{self.name}.{key}.openMidPrice"] = float("NaN")
         position[f"{self.name}.{key}.closeMidPrice"] = float("NaN")
         position[f"{self.name}.{key}.openFillPrice"] = float("NaN")
         position[f"{self.name}.{key}.closeFillPrice"] = float("NaN")
         position[f"{self.name}.{key}.openBidAskSpread"] = float("NaN")
         position[f"{self.name}.{key}.closeBidAskSpread"] = float("NaN")
         if parameters["includeLegDetails"]:
            position[f"{self.name}.{key}.midPrice.Close"] = midPrices[key]
            position[f"{self.name}.{key}.midPrice.Min"] = midPrices[key]
            position[f"{self.name}.{key}.midPrice.Avg"] = midPrices[key]
            position[f"{self.name}.{key}.midPrice.Max"] = midPrices[key]
            position[f"{self.name}.{key}.midPrice.EMA({emaMemory})"] = midPrices[key]
            position[f"{self.name}.{key}.PnL.Close"] = 0.0
            position[f"{self.name}.{key}.PnL.Min"] = 0.0
            position[f"{self.name}.{key}.PnL.Avg"] = 0.0
            position[f"{self.name}.{key}.PnL.Max"] = 0.0
            position[f"{self.name}.{key}.PnL.EMA({emaMemory})"] = 0.0
      
      # Add details about the greeks, and create placeholders to keep track of their range (Min, Avg, Max)
      #for greek in ["delta", "gamma", "vega", "theta", "rho", "vomma", "elasticity"]:
      for greek in parameters["greeksIncluded"]:
         for key in sidesDesc:
            position[f"{self.name}.{key}.{greek.title()}"] = order[f"{greek.lower()}"][key]
            if parameters["includeLegDetails"]:
               position[f"{self.name}.{key}.{greek.title()}.Close"] = order[f"{greek.lower()}"][key]
               position[f"{self.name}.{key}.{greek.title()}.Min"] = order[f"{greek.lower()}"][key]
               position[f"{self.name}.{key}.{greek.title()}.Avg"] = order[f"{greek.lower()}"][key]
               position[f"{self.name}.{key}.{greek.title()}.Max"] = order[f"{greek.lower()}"][key]
               position[f"{self.name}.{key}.{greek.title()}.EMA({emaMemory})"] = order[f"{greek.lower()}"][key]
      
       # Add details about the IV 
      for key in sidesDesc:
         position[f"{self.name}.{key}.IV"] = IVs[key]
         if trackLegDetails:
            positionTracking[f"{self.name}.{key}.IV"] = IVs[key]
         if parameters["includeLegDetails"]:
            position[f"{self.name}.{key}.IV.Close"] = IVs[key]
            position[f"{self.name}.{key}.IV.Min"] = IVs[key]
            position[f"{self.name}.{key}.IV.Avg"] = IVs[key]
            position[f"{self.name}.{key}.IV.Max"] = IVs[key]
            position[f"{self.name}.{key}.IV.EMA({emaMemory})"] = IVs[key]

      if trackLegDetails:
         positionTracking[f"{self.name}.underlyingPrice"] = underlyingPrice
         positionTracking[f"{self.name}.PnL"] = 0

      # Add this position to the global dictionary
      context.allPositions[orderId] = position
      # Add the details of this order to the openPositions dictionary.
      self.openPositions[positionKey] = order

      if trackLegDetails:
         context.positionTracking[orderId][currentDttm] = positionTracking
         
      # Keep track of all the working orders
      self.workingOrders[orderTag] = {}
      context.currentWorkingOrdersToOpen += 1
      self.currentWorkingOrdersToOpen += 1
      # Create the orders
      for contract in contracts:
         # Subscribe to the option contract data feed
         if not contract.Symbol in context.optionContractsSubscriptions:
            context.AddOptionContract(contract.Symbol, context.timeResolution)
            context.optionContractsSubscriptions.append(contract.Symbol)

         # Get the contract side (Long/Short)
         orderSide = contractSide[contract.Symbol]
         # Map each contract to the openPosition dictionary (key: expiryStr) 
         self.workingOrders[orderTag][contract.Symbol] = {"positionKey": positionKey
                                                          , "orderId": orderId
                                                          , "orderSide": orderSide
                                                          , "expiryStr" : expiryStr
                                                          , "orderType": "open"
                                                          , "fills": 0
                                                          }

         if useMarketOrders and orderSide != 0:
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

      # Stop the timer
      self.context.executionTimer.stop()

   def manageLimitOrders(self):

      # Start the timer
      self.context.executionTimer.start()

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
         prices = np.array(list(map(self.contractUtils.midPrice, contracts)))
         # Get the order sides
         orderSides = np.array(limitOrder["orderSides"])
         # Total slippage
         totalSlippage = sum(abs(orderSides)) * slippage
         # Compute the total order price (including slippage)
         midPrice = transactionSign * sum(orderSides * prices) - totalSlippage
         # Compute Bid-Ask spread
         bidAskSpread = sum(list(map(self.contractUtils.bidAskSpread, contracts)))
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

            # Store the Bid-Ask spread at the time of executing the order
            position[f"{orderType}OrderBidAskSpread"] = bidAskSpread
            # Store the price of the underlying at the time of submitting the Market Order
            position[f"underlyingPriceAt{orderType.title()}"] = context.Securities[context.underlyingSymbol].Close
            # Initialize the counter
            n = 0
            for contract in contracts:
               # Set the order side: -1 -> Sell, +1 -> Buy
               orderSide = orderSign * orderSides[n]
               # Send the Market order (asynchronous = True -> does not block the execution in case of partial fills)
               if orderSide != 0:
                  context.MarketOrder(contract.Symbol, orderSide * orderQuantity, asynchronous = True, tag = orderTag)
               # Increment the counter
               n += 1
            ### for contract in contracts

            # Remove the order from the self.limitOrders dictionary
            self.limitOrders.pop(orderTag)

      # Stop the timer
      self.context.executionTimer.stop()

   def updateContractStats(self, bookPosition, openPosition, contract, orderType = None, fillPrice = None):
   
      # Start the timer
      self.context.executionTimer.start()

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters
      
      orderId = openPosition["orderId"]
      
      # Get the side of the contract at the time of opening: -1 -> Short   +1 -> Long
      contractSide = openPosition["contractSide"][contract.Symbol]
      contractSideDesc = openPosition["contractSideDesc"][contract.Symbol]
      orderQuantity = openPosition["orderQuantity"]
      
      # Set the prefix used to identify each field to be updated
      fieldPrefix = f"{self.name}.{contractSideDesc}"

      # Store the Open/Close Fill Price (if specified)
      closeFillPrice = None
      if orderType != None:
         bookPosition[f"{fieldPrefix}.{orderType}MidPrice"] = self.contractUtils.midPrice(contract)
         bookPosition[f"{fieldPrefix}.{orderType}BidAskSpread"] = self.contractUtils.bidAskSpread(contract)
         bookPosition[f"{fieldPrefix}.{orderType}FillPrice"] = fillPrice
         if orderType == "close":
            closeFillPrice = fillPrice

      # Exit if we don't need to include the details
      if not parameters["includeLegDetails"] or context.Time.minute % parameters["legDatailsUpdateFrequency"] != 0:
         return
         
      # Get the EMA memory factor
      emaMemory = parameters["emaMemory"]
      # Compute the decay such that the contribution of each new value drops to 5% after emaMemory iterations
      emaDecay = 0.05**(1.0/emaMemory)
      
      # Update the counter (used for the average)
      bookPosition["statsUpdateCount"] += 1
      statsUpdateCount = bookPosition["statsUpdateCount"]
            
      # Compute the mid-price of the contract
      midPrice = self.contractUtils.midPrice(contract)
      # Use the fill price if the position has been closed, else use the midPrice for the intermediate PnL calculations
      closeFillPrice = closeFillPrice or midPrice * np.sign(contractSide)
      

      # Compute the Greeks (retrieve it as a dictionary)
      greeks = self.bsm.computeGreeks(contract).__dict__
      # Add the midPrice and PnL values to the greeks dictionary to generalize the processing loop
      greeks["midPrice"] = midPrice
      
      # List of variables for which we are going to update the stats
      #vars = ["midPrice", "Delta", "Gamma", "Vega", "Theta", "Rho", "Vomma", "Elasticity", "IV"]
      vars = [var.title() for var in parameters["greeksIncluded"]] + ["midPrice", "IV"]
      
      # Get the fill price at the open
      openFillPrice = bookPosition[f"{fieldPrefix}.openFillPrice"]
      # Check if the fill price is set 
      if not math.isnan(openFillPrice):
         # Compute the PnL of the contract (100 shares per contract)
         PnL = 100 * (openFillPrice + closeFillPrice)*abs(contractSide)*orderQuantity
         
         # Add the PnL to the list of variables for which we want to update the stats
         vars.append("PnL")         
         greeks["PnL"] = PnL
      
      for var in vars:
         # Set the name of the field to be updated
         fieldName = f"{fieldPrefix}.{var}"
         # Get the latest value from the dictionary
         fieldValue = greeks[var]
         # Special case for the PnL
         if var == "PnL" and statsUpdateCount == 2:
            # Initialize the EMA for the PnL
            bookPosition[f"{fieldName}.EMA({emaMemory})"] = fieldValue
         # Update the Min field
         bookPosition[f"{fieldName}.Min"] = min(bookPosition[f"{fieldName}.Min"], fieldValue)
         # Update the Max field
         bookPosition[f"{fieldName}.Max"] = max(bookPosition[f"{fieldName}.Max"], fieldValue)
         # Update the Close field (this is the most recent value of the greek)
         bookPosition[f"{fieldName}.Close"] = fieldValue
         # Update the EMA field (IMPORTANT: this must be done before we update the Avg field!)
         bookPosition[f"{fieldName}.EMA({emaMemory})"] = emaDecay * bookPosition[f"{fieldName}.EMA({emaMemory})"] + (1-emaDecay)*fieldValue
         # Update the Avg field
         bookPosition[f"{fieldName}.Avg"] = (bookPosition[f"{fieldName}.Avg"]*(statsUpdateCount-1) + fieldValue)/statsUpdateCount
         if parameters["trackLegDetails"] and var == "IV":
            if context.Time not in context.positionTracking[orderId]:
               context.positionTracking[orderId][context.Time] = {"orderId": orderId
                                                                  , "Time": context.Time
                                                                  }
            context.positionTracking[orderId][context.Time][fieldName] = fieldValue
     
      # Stop the timer
      self.context.executionTimer.stop()

   def getPositionValue(self, position):
      # Start the timer
      self.context.executionTimer.start()

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
         bidAskSpread += self.contractUtils.bidAskSpread(contract)
         # Get the latest mid-price
         midPrice = self.contractUtils.midPrice(contract)
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

      # Stop the timer
      self.context.executionTimer.stop()

      return positionDetails
   