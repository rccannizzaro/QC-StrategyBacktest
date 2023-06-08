#region imports
from AlgorithmImports import *
#endregion

########################################################################################
# Licensed under the Apache License, Version 2.0 (the "License");                      #
# Copyright [2021] [Rocco Claudio Cannizzaro]                                          #
########################################################################################

import re
import numpy as np
from Logger import *
from OptionStrategyCore import *

class OptionStrategy(OptionStrategyCore):

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
            # Increment the global counter of active positions
            context.currentActivePositions += 1
            # Decrease the global portfolio counter for the working orders to open
            context.currentWorkingOrdersToOpen -= 1
            # Increment the internal (stategy specific) counter of active positions
            self.currentActivePositions += 1
            # Marks the date/time of the most recenlty opened position 
            self.lastOpenedDttm = context.Time
            # Decrease the internal (stategy specific) counter for the working orders to open
            self.currentWorkingOrdersToOpen -= 1
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
         # Decrement the global counter of active positions
         context.currentActivePositions -= 1
         # Decrement the internal (strategy specfic) counter of active positions
         self.currentActivePositions -= 1
         
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
         # Store the Bid-Ask spread at the time of executing the order
         bookPosition["closeOrderBidAskSpread"] = bidAskSpread

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
         if useMarketOrders and orderSide != 0:
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

   def isStopLoss(self, openPosition, positionValue):
      # Get the strategy parameters
      parameters = self.parameters
   
      # Get the Stop Loss multiplier
      stopLossMultiplier = parameters["stopLossMultiplier"]
      capStopLoss = parameters["capStopLoss"]
      
      # Get the amount of credit received to open the position
      openPremium = openPosition["open"]["premium"]
      # Get the quantity used to open the position
      positionQuantity = openPosition["orderQuantity"]
      # Maximum Loss (pre-computed at the time of creating the order)
      maxLoss = openPosition["maxLoss"] * positionQuantity
      if capStopLoss:
         # Add the premium to compute the net loss
         netMaxLoss = maxLoss + openPremium
      else:
         netMaxLoss = float("-Inf")
   
      stopLoss = None
      # Check if we are using a stop loss
      if stopLossMultiplier != None:
         # Set the stop loss amount
         stopLoss = -abs(openPremium) * stopLossMultiplier
         
      # Extract the positionPnL (per share)
      positionPnL = positionValue["positionPnL"]

      # Check if we've hit the stop loss threshold
      stopLossFlg = False
      if stopLoss != None and netMaxLoss <= positionPnL <= stopLoss:
         stopLossFlg = True
   
      return stopLossFlg

   def managePositions(self):
      # Start the timer
      self.context.executionTimer.start()

      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters
      
      managePositionFrequency = max(parameters["managePositionFrequency"], 1)

      # Continue the processing only if we are at the specified schedule
      if context.Time.minute % managePositionFrequency != 0:
         return

      # Manage any Limit orders that have not been executed
      self.manageLimitOrders()
      
      # Flag to control whether we need to manage the limit orders again at the end of the loop below
      manageLimitOrders = False

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

               # Get the amount of credit received to open the position
               openPremium = position["open"]["premium"]
               
               # Get the target profit amount (if it has been set at the time of creating the order)
               targetProfit = position.get("targetProfit", None)
               # Set the target profit amount if the above step returned no value
               if targetProfit == None and parameters["profitTarget"] != None:
                  targetProfit = abs(openPremium) * parameters["profitTarget"]
               
               # Get the current value of the position
               positionDetails = self.getPositionValue(position)
               # Extract the positionPnL (per share)
               positionPnL = positionDetails["positionPnL"]

               # Exit if the positionPnL is not available (bid-ask spread is too wide)
               if positionPnL == None:
                  return

               # Keep track of the P&L range throughout the life of the position (mark the DIT of when the Min/Max PnL occurs)
               if 100*positionPnL < bookPosition["P&L.Max"]:
                  bookPosition["P&L.Min.DIT"] = currentDit
                  bookPosition["P&L.Min"] = min(bookPosition["P&L.Min"], 100*positionPnL)
               if 100*positionPnL > bookPosition["P&L.Max"]:
                  bookPosition["P&L.Max.DIT"] = currentDit
                  bookPosition["P&L.Max"] = max(bookPosition["P&L.Max"], 100*positionPnL)

               # Initialize the closeReason
               closeReason = None
               
               # Check if we've hit the stop loss threshold
               stopLossFlg = self.isStopLoss(position, positionDetails)
               if stopLossFlg:
                  closeReason = "Stop Loss trigger"
                  
               # Check if we hit the profit target
               profitTargetFlg = False
               if targetProfit != None and positionPnL >= targetProfit:
                  profitTargetFlg = True
                  closeReason = "Profit target"

               hardDitStopFlg = False
               softDitStopFlg = False
               # Check for DTE stop
               if (parameters["ditThreshold"] != None # The dteThreshold has been specified
                   and parameters["dte"] > parameters["ditThreshold"] # We are using the dteThreshold only if the open DTE was larger than the threshold
                   and currentDit >= parameters["ditThreshold"] # We have reached the DTE threshold
                   ):
                  # Check if this is a hard DTE cutoff
                  if (parameters["forceDitThreshold"] == True
                      or (parameters["hardDitThreshold"] != None and currentDit >= parameters["hardDitThreshold"])
                      ):
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
               expiryCutoffFlg = context.Time >= position["expiryMarketCloseCutoffDttm"]
               if expiryCutoffFlg:
                  closeReason = closeReason or "Expiration date cutoff"

               # Check if this is the last trading day before expiration and we have reached the cutoff time
               endOfBacktestCutoffFlg = False
               if self.endOfBacktestCutoffDttm != None and context.Time >= self.endOfBacktestCutoffDttm:
                  endOfBacktestCutoffFlg = True
                  closeReason = closeReason or "End of Backtest Liquidation"
                  # Set the stopLossFlg = True to force a Market Order 
                  stopLossFlg = True

               # Update the stats of each contract
               if parameters["includeLegDetails"] and context.Time.minute % parameters["legDatailsUpdateFrequency"] == 0:
                  for contract in position["contracts"]:
                     self.updateContractStats(bookPosition, position, contract)
                  if parameters["trackLegDetails"]:
                     underlyingPrice = context.GetLastKnownPrice(context.Securities[context.underlyingSymbol]).Price
                     context.positionTracking[orderId][context.Time][f"{self.name}.underlyingPrice"] = underlyingPrice
                     context.positionTracking[orderId][context.Time][f"{self.name}.PnL"] = positionPnL

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
                  # Need to manage any Limit orders that have been added
                  manageLimitOrders = True

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
                     self.currentWorkingOrdersToOpen -= 1
                     self.workingOrders.pop(orderTag)
                  # Mark the order as being cancelled
                  context.allPositions[orderId]["orderCancelled"] = True
                  # Remove the cancelled position from the final output unless we are required to include it
                  if not parameters["includeCancelledOrders"]:
                     context.allPositions.pop(orderId)
               ### if context.Time > position["open"]["limitOrderExpiryDttm"]
            ### No fills at all
         ### The open position has not been fully filled (this must be a Limit order)
      
      # Manage any Limit orders that have been created in the meantime
      if manageLimitOrders:
         self.manageLimitOrders()
      
      # Stop the timer
      self.context.executionTimer.stop()
