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
from OptionStrategyOrder import *
from ContractUtils import *

class OptionStrategy(OptionStrategyOrder):

   def run(self, chain, expiryList = None):
      # Start the timer
      self.context.executionTimer.start()

      # Get the context
      context = self.context
      # Initialize the contract utils
      self.contractUtils = ContractUtils(context)
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

      # Check if the epiryList was specified as an input
      if expiryList == None:
         # List of expiry dates, sorted in reverse order
         expiryList = sorted(set([contract.Expiry for contract in chain
                                    if minDte <= (contract.Expiry.date() - self.Time.date()).days <= maxDte
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
      underlyingPrice = contracts[0].UnderlyingLastPrice

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
                  , "statsUpdateCount"      : 1.0
                  }

      # Using separate loops here so that the final CSV file has the columns in the desired order
      # Add details about strikes of each contract in the order
      for key in sidesDesc:
         position[f"{self.name}.{key}.Strike"] = strikes[key]

      # Add details about the mid price, fill price and related stats 
      for key in sidesDesc:
         position[f"{self.name}.{key}.openFillPrice"] = float("NaN")
         position[f"{self.name}.{key}.closeFillPrice"] = float("NaN")
         position[f"{self.name}.{key}.midPrice"] = midPrices[key]
         if parameters["includeLegDetails"]:
            position[f"{self.name}.{key}.midPrice.Min"] = midPrices[key]
            position[f"{self.name}.{key}.midPrice.Avg"] = midPrices[key]
            position[f"{self.name}.{key}.midPrice.Max"] = midPrices[key]
            position[f"{self.name}.{key}.midPrice.EMA({emaMemory})"] = midPrices[key]
            position[f"{self.name}.{key}.PnL"] = 0.0
            position[f"{self.name}.{key}.PnL.Min"] = 0.0
            position[f"{self.name}.{key}.PnL.Avg"] = 0.0
            position[f"{self.name}.{key}.PnL.Max"] = 0.0
            position[f"{self.name}.{key}.PnL.EMA({emaMemory})"] = 0.0
      
      # Add details about the greeks, and create placeholders to keep track of their range (Min, Avg, Max)
      for greek in ["delta", "gamma", "vega", "theta", "rho", "vomma", "elasticity"]:
         for key in sidesDesc:
            position[f"{self.name}.{key}.{greek.title()}"] = order[f"{greek}"][key]
            if parameters["includeLegDetails"]:
               position[f"{self.name}.{key}.{greek.title()}.Min"] = order[f"{greek}"][key]
               position[f"{self.name}.{key}.{greek.title()}.Avg"] = order[f"{greek}"][key]
               position[f"{self.name}.{key}.{greek.title()}.Max"] = order[f"{greek}"][key]
               position[f"{self.name}.{key}.{greek.title()}.EMA({emaMemory})"] = order[f"{greek}"][key]
            
       # Add details about the IV 
      for key in sidesDesc:
         position[f"{self.name}.{key}.IV"] = IVs[key]
         if parameters["includeLegDetails"]:
            position[f"{self.name}.{key}.IV.Min"] = IVs[key]
            position[f"{self.name}.{key}.IV.Avg"] = IVs[key]
            position[f"{self.name}.{key}.IV.Max"] = IVs[key]
            position[f"{self.name}.{key}.IV.EMA({emaMemory})"] = IVs[key]

      # Add this position to the global dictionary
      context.allPositions[orderId] = position
      # Add the details of this order to the openPositions dictionary.
      self.openPositions[positionKey] = order

      # Keep track of all the working orders
      self.workingOrders[orderTag] = {}
      context.currentWorkingOrdersToOpen += 1
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

      # Stop the timer
      self.context.executionTimer.stop()


   def updateContractStats(self, bookPosition, openPosition, contract, orderType = None, fillPrice = None):
   
      # Start the timer
      self.context.executionTimer.start()

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters
      
      # Get the side of the contract at the time of opening: -1 -> Short   +1 -> Long
      contractSide = openPosition["contractSide"][contract.Symbol]
      contractSideDesc = openPosition["contractSideDesc"][contract.Symbol]
      
      # Set the prefix used to identify each field to be updated
      fieldPrefix = f"{self.name}.{contractSideDesc}"

      # Store the Open/Close Fill Price (if specified)
      if orderType != None:
         bookPosition[f"{fieldPrefix}.{orderType}FillPrice"] = fillPrice

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

      # Compute the Greeks (retrieve it as a dictionary)
      greeks = self.bsm.computeGreeks(contract).__dict__
      # Add the midPrice and PnL values to the greeks dictionary to generalize the processing loop
      greeks["midPrice"] = midPrice
      
      # List of variables for which we are going to update the stats
      vars = ["midPrice", "Delta", "Gamma", "Vega", "Theta", "Rho", "Vomma", "Elasticity", "IV"]
      
      # Get the fill price at the open
      openFillPrice = bookPosition[f"{fieldPrefix}.openFillPrice"]
      # Check if the fill price is set 
      if not math.isnan(openFillPrice):
         # Compute the PnL of the contract (100 shares per contract)
         PnL = 100 * (openFillPrice + midPrice * contractSide)
         # Add the PnL to the list of variables for which we want to update the stats
         vars.append("PnL")         
         greeks["PnL"] = PnL
      
      for var in vars:
         # Set the name of the field to be updated
         fieldName = f"{fieldPrefix}.{var}"
         # Get the latest value from the dictionary
         fieldValue = greeks[var]
         # Special case for the PnL
         if var == "PnL":
            # Update the PnL value
            bookPosition[f"{fieldName}"] = fieldValue
            # Initialize the EMA for the PnL
            if statsUpdateCount == 2:
               bookPosition[f"{fieldName}.EMA({emaMemory})"] = fieldValue
         # Update the Min field
         bookPosition[f"{fieldName}.Min"] = min(bookPosition[f"{fieldName}.Min"], fieldValue)
         # Update the Max field
         bookPosition[f"{fieldName}.Max"] = max(bookPosition[f"{fieldName}.Max"], fieldValue)
         # Update the EMA field (IMPORTANT: this must be done before we update the Avg field!)
         bookPosition[f"{fieldName}.EMA({emaMemory})"] = emaDecay * bookPosition[f"{fieldName}.EMA({emaMemory})"] + (1-emaDecay)*fieldValue
         # Update the Avg field
         bookPosition[f"{fieldName}.Avg"] = (bookPosition[f"{fieldName}.Avg"]*(statsUpdateCount-1) + fieldValue)/statsUpdateCount
     
      # Stop the timer
      self.context.executionTimer.stop()

   def handleOrderEvent(self, orderEvent):

      # Start the timer
      self.context.executionTimer.start()

      # Process only Fill events 
      if not (orderEvent.Status == OrderStatus.Filled or orderEvent.Status == OrderStatus.PartiallyFilled):
         return

      if(orderEvent.IsAssignment):
         # TODO: Liquidate the assigned position. 
         #  Eventually figure out which open position it belongs to and close that position.
         return

      # Get the context
      context = self.context

      # Get the orderEvent id
      orderEventId = orderEvent.OrderId
      # Retrieve the order associated to this events
      order = context.Transactions.GetOrderById(orderEventId)
      # Get the order tag. Remove any warning text that might have been added in case of Fills at Stale Price
      orderTag = re.sub(" - Warning.*", "", order.Tag)

      # Get the working order (if available)
      workingOrder = self.workingOrders.get(orderTag)
      # Exit if this order tag is not in the list of open orders.
      if workingOrder == None:
         return

      contractInfo = workingOrder.get(orderEvent.Symbol)
      # Exit if we couldn't find the contract info.
      if contractInfo == None:
         return

      # Get the order id and expiryStr value for the contract
      orderId = contractInfo["orderId"]
      positionKey = contractInfo["positionKey"]
      expiryStr = contractInfo["expiryStr"]
      orderType = contractInfo["orderType"]

      # Log the order event
      self.logger.debug(f" -> Processing order id {orderId} (orderTag: {orderTag}  -  orderType: {orderType}  -  Expiry: {expiryStr})")

      # Exit if this expiry date is not in the list of open positions
      if positionKey not in self.openPositions:
         return

      # Retrieve the open position
      openPosition = self.openPositions[positionKey]
      # Retrieved the book position (this it the full entry inside allPositions that will be converted into a CSV record)
      bookPosition = context.allPositions[orderId]
      
      # Get the contract associated to this order event
      contract = openPosition["contractDictionary"][orderEvent.Symbol]
      # Get the description associated with this contract
      contractDesc = openPosition["contractSideDesc"][orderEvent.Symbol]
      # Get the quantity used to open the position
      positionQuantity = openPosition["orderQuantity"]
      # Get the side of each leg (-n -> Short, +n -> Long)
      contractSides = np.array(openPosition["sides"])
      # Leg Quantity
      legQuantity = abs(openPosition["contractSide"][orderEvent.Symbol])
      # Total legs quantity in the whole position
      Nlegs = sum(abs(contractSides))

      # Check if the contract was filled at a stale price (Warnings in the orderTag)
      if re.search(" - Warning.*", order.Tag):
         self.logger.warning(order.Tag)
         openPosition[orderType]["stalePrice"] = True
         bookPosition[f"{orderType}StalePrice"] = True

      # Add the order to the list of openPositions orders (only if this is the first time the order is filled  - in case of partial fills)
      if contractInfo["fills"] == 0:
         openPosition[orderType]["orders"].append(order)

      # Update the number of filled contracts associated with this order
      contractInfo["fills"] += abs(orderEvent.FillQuantity)

      # Remove this order entry from the self.workingOrders[orderTag] dictionary if it has been fully filled
      if contractInfo["fills"] == legQuantity * positionQuantity:
         removedOrder = self.workingOrders[orderTag].pop(orderEvent.Symbol)
         # Update the stats of the given contract inside the bookPosition (reverse the sign of the FillQuantity: Sell -> credit, Buy -> debit)
         self.updateContractStats(bookPosition, openPosition, contract, orderType = orderType, fillPrice = - np.sign(orderEvent.FillQuantity) * orderEvent.FillPrice)

      # Update the counter of positions that have been filled
      openPosition[orderType]["fills"] += abs(orderEvent.FillQuantity)
      # Get the total amount of the transaction
      transactionAmt = orderEvent.FillQuantity * orderEvent.FillPrice * 100
      # Check if this is a fill order for an entry position
      if orderType == "open":
         # Update the openPremium field to include the current transaction (use "-=" to reverse the side of the transaction: Short -> credit, Long -> debit)
         bookPosition["openPremium"] -= transactionAmt
      else: # This is an order for the exit position
         # Update the closePremium field to include the current transaction  (use "-=" to reverse the side of the transaction: Sell -> credit, Buy -> debit)
         bookPosition["closePremium"] -= transactionAmt

      # Check if all legs have been filled
      if openPosition[orderType]["fills"] == Nlegs*positionQuantity:
         openPosition[orderType]["filled"] = True
         # Remove the working order now that it has been filled
         self.workingOrders.pop(orderTag)
         # Set the time when the full order was filled
         bookPosition[orderType + "FilledDttm"] = context.Time
         # Record the order mid price
         bookPosition[orderType + "OrderMidPrice"] = openPosition[orderType]["orderMidPrice"]
         
         if orderType == "open":
            # Trigger an update of the charts
            context.statsUpdated = True
            # Increment the counter of active positions
            context.currentActivePositions += 1
            # Decrease the counter for the working orders to open
            context.currentWorkingOrdersToOpen -= 1
            # Store the credit received (needed to determine the stop loss): value is per share (divided by 100)
            openPosition[orderType]["premium"] = bookPosition["openPremium"] / 100

      # Check if the entire position has been closed
      if orderType == "close" and openPosition["open"]["filled"] and openPosition["close"]["filled"]:

         # Compute P&L for the position
         positionPnL = bookPosition["openPremium"] + bookPosition["closePremium"]

         # Store the PnL for the position
         bookPosition["P&L"] = positionPnL
         # Now we can remove the position from the self.openPositions dictionary
         removedPosition = self.openPositions.pop(positionKey)
         # Decrement the counter of active positions
         context.currentActivePositions -= 1
         
         # Compute the DTE at the time of closing the position
         closeDte = (contract.Expiry.date() - context.Time.date()).days
         # Collect closing trade info
         closeTradeInfo = {"orderTag": orderTag, "closeDte": closeDte}
         # Add this trade info to the FIFO list
         self.recentlyClosedDTE.append(closeTradeInfo)

         # ###########################
         # Collect Performance metrics
         # ###########################
         self.updateStats(removedPosition)

      # Stop the timer
      self.context.executionTimer.stop()

   def updateStats(self, closedPosition):
      # Start the timer
      self.context.executionTimer.start()

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
            updateFlg = False
            # Get the short strikes (if any)
            if("shortPut" in strikes):
               updateFlg = True
               shortPutStrike = strikes["shortPut"]
            if("shortCall" in strikes):
               updateFlg = True
               shortCallStrike = strikes["shortCall"]

            if updateFlg:
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


   def closePosition(self, positionDetails, closeReason, stopLossFlg = False):

      # Start the timer
      self.context.executionTimer.start()

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
      
      if parameters["allowMultipleEntriesPerExpiry"]:
         positionKey = orderId
      else:
         positionKey = expiryStr

      # Get the details currently open position 
      openPosition = self.openPositions[positionKey]
      # Get the book position
      bookPosition = context.allPositions[orderId]
      # Extract the expiry date
      expiry = openPosition["expiry"]
      # Get the last trading day before expiration
      expiryLastTradingDay = openPosition["expiryLastTradingDay"]
      # Get the date/time threshold by which the position must be closed (on the last trading day before expiration)
      expiryMarketCloseCutoffDttm = openPosition["expiryMarketCloseCutoffDttm"]
      
      # Get the contracts and their side
      contracts = openPosition["contracts"]
      contractSide = openPosition["contractSide"]
      # Set the expiration threshold at 15:40 of the expiration date (but no later than the market close cut-off time).
      expirationThreshold = min(expiryLastTradingDay + timedelta(hours = 15, minutes = 40), expiryMarketCloseCutoffDttm)
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
      bookPosition["closeDttm"] = context.Time
      # Set the date when the closing order is created
      bookPosition["closeDt"] = context.Time.strftime("%Y-%m-%d")
      # Set the price of the underlying at the time of submitting the order to close
      bookPosition["underlyingPriceAtOrderClose"] = priceAtClose
      # Set the price of the underlying at the time of submitting the order to close:
      # - This is the same as underlyingPriceAtOrderClose in case of Market Orders
      # - In case of Limit orders, this is the actual price of the underlying at the time when the Limit Order was triggered (price is updated later by the manageLimitOrders method)      
      bookPosition["underlyingPriceAtClose"] = priceAtClose
      # Set the mid-price of the position at the time of closing
      bookPosition["closeOrderMidPrice"] = orderMidPrice
      bookPosition["closeOrderMidPrice.Min"] = orderMidPrice
      bookPosition["closeOrderMidPrice.Max"] = orderMidPrice
      # Set the Limit Order price of the position at the time of closing
      bookPosition["closeOrderLimitPrice"] = limitOrderPrice
      # Set the close DTE
      bookPosition["closeDTE"] = (openPosition["expiry"].date() - context.Time.date()).days
      # Set the Days in Trade
      bookPosition["DIT"] = (context.Time.date() - bookPosition["openFilledDttm"].date()).days
      # Set the close reason
      bookPosition["closeReason"] = closeReason

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
         self.workingOrders[orderTag][symbol] = {"positionKey": positionKey, "orderId": orderId, "expiryStr" : expiryStr, "orderType": "close", "fills": 0}

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

      # Stop the timer
      self.context.executionTimer.stop()

   def managePositions(self):
      # Start the timer
      self.context.executionTimer.start()

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters

      # Manage any Limit orders that have not been executed
      self.manageLimitOrders()

      # Loop through all open positions
      for positionKey in list(self.openPositions):
         # Skip this contract if in the meantime it has been removed by the onOrderEvent
         if positionKey not in self.openPositions:
            continue

         # Get the position
         position = self.openPositions[positionKey]
         # Get the order id
         orderId = position["orderId"]
         # Get the order tag
         orderTag = position["orderTag"]
         # Get the book position
         bookPosition = context.allPositions[orderId]
         # How many days to expiration are left for this position
         currentDte = (position["expiry"].date() - context.Time.date()).days


         # Check if this is a fully filled position
         if position["open"]["filled"] == True:

            # How many days has this position been in trade for
            currentDit = (context.Time.date() - bookPosition["openFilledDttm"].date()).days

            # Check if we have any pending working orders to close
            if self.workingOrders.get(orderTag):

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
                     # Remove the order from the self.workingOrders dictionary
                     if orderTag in self.workingOrders:
                        self.workingOrders.pop(orderTag)
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
               # Maximum Loss (pre-computed at the time of creating the order)
               maxLoss = position["maxLoss"] * positionQuantity
               # Add the premium to compute the net loss
               netMaxLoss = maxLoss + openPremium
               
               stopLoss = None
               # Check if we are using a stop loss
               if parameters["stopLossMultiplier"] != None:
                  # Set the stop loss amount
                  stopLoss = -abs(openPremium) * parameters["stopLossMultiplier"]

               # Get the current value of the position
               positionDetails = self.getPositionValue(position)
               # Extract the positionPnL (per share)
               positionPnL = positionDetails["positionPnL"]

               # Exit if the positionPnL is not available (bid-ask spread is too wide)
               if positionPnL == None:
                  return

               # Keep track of the P&L range throughout the life of the position
               bookPosition["P&L.Min"] = min(bookPosition["P&L.Min"], 100*positionPnL)
               bookPosition["P&L.Max"] = max(bookPosition["P&L.Max"], 100*positionPnL)

               # Update the stats of each contract
               if parameters["includeLegDetails"] and context.Time.minute % parameters["legDatailsUpdateFrequency"] == 0:
                  for contract in position["contracts"]:
                     self.updateContractStats(bookPosition, position, contract)


               # Initialize the closeReason
               closeReason = None
               
               # Check if we've hit the stop loss threshold
               stopLossFlg = False
               if stopLoss != None and netMaxLoss <= positionPnL <= stopLoss:
                  stopLossFlg = True
                  closeReason = "Stop Loss trigger"
                  
               # Check if we hit the profit target
               profitTargetFlg = positionPnL >= targetProfit
               if profitTargetFlg:
                  closeReason = "Profit target"

               hardDitStopFlg = False
               softDitStopFlg = False
               # Check for DTE stop
               if (parameters["ditThreshold"] != None # The dteThreshold has been specified
                   and parameters["dte"] > parameters["ditThreshold"] # We are using the dteThreshold only if the open DTE was larger than the threshold
                   and currentDit >= parameters["ditThreshold"] # We have reached the DTE threshold
                   ):
                  # Check if this is a hard DTE cutoff
                  if parameters["forceDitThreshold"] == True:
                     hardDitStopFlg = True
                     closeReason = closeReason or "Hard DIT cutoff"
                  # Check if this is a soft DTE cutoff
                  elif positionPnL >= 0:
                     softDitStopFlg = True
                     closeReason = closeReason or "Soft DIT cutoff"

               hardDteStopFlg = False
               softDteStopFlg = False
               # Check for DTE stop
               if (parameters["dteThreshold"] != None # The dteThreshold has been specified
                   and parameters["dte"] > parameters["dteThreshold"] # We are using the dteThreshold only if the open DTE was larger than the threshold
                   and currentDte <= parameters["dteThreshold"] # We have reached the DTE threshold
                   ):
                  # Check if this is a hard DTE cutoff
                  if parameters["forceDteThreshold"] == True:
                     hardDteStopFlg = True
                     closeReason = closeReason or "Hard DTE cutoff"
                  # Check if this is a soft DTE cutoff
                  elif positionPnL >= 0:
                     softDteStopFlg = True
                     closeReason = closeReason or "Soft DTE cutoff"

               # Check if this is the last trading day before expiration and we have reached the cutoff time
               expiryCutoffFlg = context.Time > position["expiryMarketCloseCutoffDttm"]
               if expiryCutoffFlg:
                  closeReason = closeReason or "Expiration date cutoff"

               # Check if this is the last trading day before expiration and we have reached the cutoff time
               endOfBacktestCutoffFlg = False
               if self.endOfBacktestCutoffDttm != None and context.Time > self.endOfBacktestCutoffDttm:
                  endOfBacktestCutoffFlg = True
                  closeReason = closeReason or "End of Backtest Liquidation"
                  # Set the stopLossFlg = True to force a Market Order 
                  stopLossFlg = True

               # Check if we need to close the position
               if (profitTargetFlg # We hit the profit target
                   or stopLossFlg # We hit the stop loss (making sure we don't exceed the max loss in case of spreads)
                   or hardDteStopFlg # The position must be closed when reaching the DTE threshold (hard stop)
                   or softDteStopFlg # Soft DTE stop: close as soon as it is profitable
                   or hardDitStopFlg # The position must be closed when reaching the DIT threshold (hard stop)
                   or softDitStopFlg # Soft DIT stop: close as soon as it is profitable
                   or expiryCutoffFlg # This is the last trading day before expiration, we have reached the cutoff time
                   or endOfBacktestCutoffFlg # This is the last trading day before the end of the backtest -> Liquidate all positions
                   ):
                  # Close the position
                  self.closePosition(positionDetails, closeReason, stopLossFlg = stopLossFlg)

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
                  if positionKey in self.openPositions:
                     self.openPositions.pop(positionKey)
                  # Remove the order from the self.workingOrders dictionary
                  if orderTag in self.workingOrders:
                     context.currentWorkingOrdersToOpen -= 1
                     self.workingOrders.pop(orderTag)
                  # Mark the order as being cancelled
                  context.allPositions[orderId]["orderCancelled"] = True
                  # Remove the cancelled position from the final output unless we are required to include it
                  if not parameters["includeCancelledOrders"]:
                     context.allPositions.pop(orderId)
               ### if context.Time > position["open"]["limitOrderExpiryDttm"]
            ### No fills at all
         ### The open position has not been fully filled (this must be a Limit order)
         
      # Stop the timer
      self.context.executionTimer.stop()

