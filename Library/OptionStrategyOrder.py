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
from Logger import *
from BSMLibrary import *
from StrategyBuilder import *
from ContractUtils import *
from OptionStrategyOrderCore import *

class OptionStrategyOrder(OptionStrategyOrderCore):

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
      calls = self.strategyBuilder.getSpread(contracts, "Call", strike = puts[-1].Strike, wingSize = callWingSize)

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

