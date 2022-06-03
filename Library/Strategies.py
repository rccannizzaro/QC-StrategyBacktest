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

from OptionStrategy import *
from System.Drawing import Color

class PutStrategy(OptionStrategy):
   def getOrder(self, chain):
      return self.getNakedOrder(chain
                                , "Put"
                                , delta = self.parameters["delta"]
                                , sell = self.parameters["creditStrategy"]
                                )


class CallStrategy(OptionStrategy):
   def getOrder(self, chain):
      return self.getNakedOrder(chain
                                , "Call"
                                , delta = self.parameters["delta"]
                                , sell = self.parameters["creditStrategy"]
                                )


class StraddleStrategy(OptionStrategy):
   def getOrder(self, chain):
      return self.getStraddleOrder(chain
                                   , netDelta = self.parameters["netDelta"]
                                   , sell = self.parameters["creditStrategy"]
                                   )


class StrangleStrategy(OptionStrategy):
   def getOrder(self, chain):
      return self.getStrangleOrder(chain
                                   , callDelta = self.parameters["callDelta"]
                                   , putDelta = self.parameters["putDelta"]
                                   , sell = self.parameters["creditStrategy"]
                                   )


class PutSpreadStrategy(OptionStrategy):
   def getOrder(self, chain):
      return self.getSpreadOrder(chain
                                 , "Put"
                                 , delta = self.parameters["delta"]
                                 , wingSize = self.parameters["wingSize"]
                                 , sell = self.parameters["creditStrategy"]
                                 )


class CallSpreadStrategy(OptionStrategy):
   def getOrder(self, chain):
      return self.getSpreadOrder(chain
                                 , "Call"
                                 , delta = self.parameters["delta"]
                                 , wingSize = self.parameters["wingSize"]
                                 , sell = self.parameters["creditStrategy"]
                                 )


class IronCondorStrategy(OptionStrategy):
   def getOrder(self, chain):
      return self.getIronCondorOrder(chain
                                     , callDelta = self.parameters["callDelta"]
                                     , putDelta = self.parameters["putDelta"]
                                     , callWingSize = self.parameters["callWingSize"]
                                     , putWingSize = self.parameters["putWingSize"]
                                     , sell = self.parameters["creditStrategy"]
                                     )


class IronFlyStrategy(OptionStrategy):
   def getOrder(self, chain):
      return self.getIronFlyOrder(chain
                                  , netDelta = self.parameters["netDelta"]
                                  , callWingSize = self.parameters["callWingSize"]
                                  , putWingSize = self.parameters["putWingSize"]
                                  , sell = self.parameters["creditStrategy"]
                                  )
      
class ButterflyStrategy(OptionStrategy):
   def getOrder(self, chain):
      return self.getButterflyOrder(chain
                                  , netDelta = self.parameters["netDelta"]
                                  , type = self.parameters["butteflyType"]
                                  , leftWingSize = self.parameters["butterflyLeftWingSize"]
                                  , rightWingSize = self.parameters["butterflyRightWingSize"]
                                  , sell = self.parameters["creditStrategy"]
                                  )




class TEBombShelterStrategy(OptionStrategy):

   def run(self, chain, expiryList = None):
   
      context = self.context
      
      if expiryList == None:
         # List of expiry dates, sorted in reverse order
         expiryList = sorted(set([contract.Expiry for contract in chain]), reverse = True)
         # Log the list of expiration dates found in the chain
         self.logger.debug("Expiration dates in the chain:")
         for expiry in expiryList:
            self.logger.debug(f" -> {expiry}")

      # Get the furthest expiry date (Back cycle)
      backExpiry = expiryList[0]
      
      # Get the list of expiry dates that are within the front-cycle DTE requirement
      frontExpiryList = sorted([expiry for expiry in expiryList 
                                   if (expiry.date() - context.Time.date()).days <= self.parameters["frontDte"]
                               ]
                               , reverse = True
                               )

      # Exit if we could not find any front-cycle expiration
      if not frontExpiryList:
         return
      
      # Get the furthest expiry date (Front cycle)
      frontExpiry = frontExpiryList[0]
      
      # Convert the date to a string
      expiryStr = frontExpiry.strftime("%Y-%m-%d")

      # Proceed if we have not already opened a position on the given expiration
      if(self.parameters["allowMultipleEntriesPerExpiry"] or expiryStr not in self.openPositions):
         # Filter the contracts in the chain, keep only the ones expiring on the back-cycle
         backChain = self.filterByExpiry(chain, expiry = backExpiry, computeGreeks = True)
         # Filter the contracts in the chain, keep only the ones expiring on the front-cycle
         frontChain = self.filterByExpiry(chain, expiry = frontExpiry, computeGreeks = True)
         
         # Call the getOrder method of this class
         order = self.getOrder(backChain, frontChain)
         # Execute the order
         self.openPosition(order)
   



   def getOrder(self, backChain, frontChain):
   
      # Theta Engine + Bomb Shelter combo: 1 short Put (back-cycle) and buy 2 long puts (front-cycle)
      sides = [-1, 2]
      strategy = "TE Bomb Shelter"

      # Get the Strategy parameters
      parameters = self.parameters
      
      # Set the Delta for the short Put
      delta = parameters["delta"]
      # Percentage of the premium alloated to the Bomb Shelter hedge
      hedgeAllocation = parameters["hedgeAllocation"] or 0.0

      # Get all Puts (back cycle) with a Delta lower than the given delta
      back_contracts = self.strategyBuilder.getPuts(backChain, toDelta = delta)
      
      # Exit if we could not find a Put matching the specified Delta criteria
      if not back_contracts:
         return

      # Get the short put (back cycle)
      shortPut = back_contracts[0]
      # Get the mid-price of the short put
      midPrice = self.contractUtils.midPrice(shortPut)
      # Set the target price for the long Puts
      targetLongPrice = midPrice * hedgeAllocation / 2
      
      # Get all Puts (front cycle) with a price 
      front_contracts = self.strategyBuilder.getPuts(frontChain, toPrice = targetLongPrice)

      # Exit if we could not find a Put matching the specified price criteria
      if not front_contracts:
         return

      # Get the long put (front cycle)
      longPut = front_contracts[0]
      
      # Create order details
      order = self.getOrderDetails([shortPut, longPut], sides, strategy, sell = True, expiry = longPut.Expiry)
      
      # Return the order
      return order      

   # Add BombShelter custom charts
   def setupCharts(self):

      # Keep track of all the time series
      self.TEBSPlotCount = 0
      # Create a plot to chart the PnL components of this strategy
      self.TEBSPlotSummary = Chart("TE Bomb Shelter Summary")
      self.TEBSPlotSummary.AddSeries(Series("Theta Engine PnL", SeriesType.Line, self.TEBSPlotCount))
      self.TEBSPlotSummary.AddSeries(Series("Hedge PnL", SeriesType.Line, self.TEBSPlotCount))
      self.TEBSPlotSummary.AddSeries(Series("Bomb Shelter PnL", SeriesType.Line, self.TEBSPlotCount))
      # Add a plot to chrt the value of each leg
      self.TEBSPlotDetails = Chart("TE Bomb Shelter Details")
      
   # Update BombShelter custom charts
   def updateCharts(self):
      # Get the context
      context = self.context
      # Get the strategy parameters
      parameters = self.parameters
      
      # Get the chart update frequency
      chartUpdateFrequency = parameters.get("chartUpdateFrequency")
       # Only run this at the specified frequency 
      if chartUpdateFrequency == None or context.Time.minute % chartUpdateFrequency != 0:
         return
      
      
      # Compute the total PnL of the Short positions across the entire book (excluding cancelled orders)
      shortPnL = sum(list(map(lambda bookPosition: 
                                 bookPosition.get(f"{self.name}.shortPut.PnL", 0) * bookPosition["orderQuantity"] * int(not bookPosition["orderCancelled"])
                              , context.allPositions.values()
                              )
                          )
                     )
      # Compute the total PnL of the two Long positions across the entire book (excluding cancelled orders)
      longPnL = sum(list(map(lambda bookPosition: 
                                 bookPosition.get(f"{self.name}.longPut.PnL", 0) * 2 * bookPosition["orderQuantity"] * int(not bookPosition["orderCancelled"])
                              , context.allPositions.values()
                              )
                          )
                     )
      # Compute the net PnL
      netPnL = shortPnL + longPnL
      
      # Plot the current value of the option contracts
      context.Plot("TE Bomb Shelter Summary", "Theta Engine PnL", shortPnL)
      context.Plot("TE Bomb Shelter Summary", "Hedge PnL", longPnL)
      context.Plot("TE Bomb Shelter Summary", "Bomb Shelter PnL", netPnL)
      
      # Loop through all the open positions (specific to this strategy)
      for positionKey in list(self.openPositions):
         if positionKey in self.openPositions:
            # Extract the open position and the order id
            openPosition = self.openPositions[positionKey]
            orderId = openPosition["orderId"]
            # Retrieve the position details from the book
            bookPosition = context.allPositions[orderId]

            # Skip plotting this position if it's not yet filled or if it was filled at a stale price
            if not openPosition["open"]["filled"] or openPosition["open"]["stalePrice"]:
               continue
            
            # Get the parameter plotLegDetails (if defined). Set the default to False if not defined.
            plotLegDetails = parameters.get("plotLegDetails", False)
            
            # Exit if we don't need to plot the details of each leg
            if not plotLegDetails:
               return
               
            # Get the Short and Long contracts
            shortPut = openPosition["contracts"][0]
            longPut = openPosition["contracts"][1]
            
            # Compute the current value of the contracts (the Long has two contracts)
            shortValue = self.contractUtils.midPrice(shortPut)
            longValue = self.contractUtils.midPrice(longPut) * 2
            
            # Define the stats variable names
            shortVarName = f"TEBS_shortPut_{orderId}"
            longVarName = f"TEBS_longPut_{orderId}"
            
            # Check if this is the first time that we are potting this data
            if not hasattr(context.stats, shortVarName):
               # Increase the plot counter. Each position will be plotted on a separate subplot (use TEBSPlotCount as the plot index level)
               self.TEBSPlotCount += 1
               # Add the time series
               self.TEBSPlotDetails.AddSeries(Series(f"{orderId} - Short Put ({shortPut.Strike})", SeriesType.Line, self.TEBSPlotCount))
               self.TEBSPlotDetails.AddSeries(Series(f"{orderId} - Long Put Hedge ({longPut.Strike})", SeriesType.Line, self.TEBSPlotCount))
               
            # Set/Update the stats variable
            setattr(context.stats, shortVarName, shortValue)
            setattr(context.stats, longVarName, longValue)
            
            # Plot the current value of the option contracts
            context.Plot("TE Bomb Shelter Details", f"{orderId} - Short Put (Strike: {int(shortPut.Strike)})", shortValue)
            context.Plot("TE Bomb Shelter Details", f"{orderId} - Long Put Hedge (Strike: {int(longPut.Strike)})", longValue)
            
