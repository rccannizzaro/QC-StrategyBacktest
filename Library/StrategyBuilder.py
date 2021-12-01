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

from Logger import *

class StrategyBuilder:

   # \param[in] context is a reference to the QCAlgorithm instance. The following attributes are used from the context:
   #    - slippage: (Optional) controls how the mid-price of an order is adjusted to include slippage.
   #    - targetPremium: (Optional) used to determine how many contracts to buy/sell.  
   #    - maxOrderQuantity: (Optional) Caps the number of contracts that are bought/sold (Default: 1). 
   #         If targetPremium == None  -> This is the number of contracts bought/sold.
   #         If targetPremium != None  -> The order is executed only if the number of contracts required to reach the target credit/debit does not exceed the maxOrderQuantity
   def __init__(self, context):
      # Set the context (QCAlgorithm object)
      self.context = context
      # Set the logger
      self.logger = Logger(context, className = type(self).__name__, logLevel = context.logLevel)


   # Returns the mid-price of an option contract
   def midPrice(self, contract):
      return 0.5*(contract.BidPrice + contract.AskPrice)


   # Returns the mid-price of an option contract
   def bidAskSpread(self, contract):
      return abs(contract.AskPrice - contract.BidPrice)


   # Returns True/False based on whether the option contract is of the specified type (Call/Put)
   def optionTypeFilter(self, contract, type = None):
      if type == None:
         return True

      type = type.lower()
      if type == "put":
         return contract.Right == OptionRight.Put 
      elif type == "call":
         return contract.Right == OptionRight.Call
      else:
         return True


   # Return the ATM contracts (Put/Call or both)
   def getATM(self, contracts, type = None):

      # Initialize result
      atm_contracts = []

      # Sort the contracts based on how close they are to the current price of the underlying. 
      # Filter them by the selected contract type (Put/Call or both)
      sorted_contracts = sorted([contract 
                                    for contract in filteredChain 
                                       if self.optionTypeFilter(contract, type)
                                 ]
                                , key = lambda x: abs(x.Strike - x.UnderlyingLastPrice)
                                , reverse = False
                                )

      # Check if any contracts were returned after the filtering
      if len(sorted_contracts) > 0:
         if type == None or type.lower() == "both":
            # Select the first two contracts (one Put and one Call)
            Ncontracts = min(len(sorted_contracts), 2)
         else:
            # Select the first contract (either Put or Call, based on the type specified)
            Ncontracts = 1
         # Extract the selected contracts
         atm_contracts = sorted_contracts[0:Ncontracts]
      # Return result
      return atm_contracts


   def getATMStrike(self, contracts):
      ATMStrike = None
      # Get the ATM contracts
      atm_contracts = self.getATM(contracts)
      # Check if any contracts were found
      if len(atm_contracts) > 0:
         # Get the Strike of the first contract
         ATMStrike = atm_contracts[0].Strike
      # Return result
      return ATMStrike


   def getContracts(self, contracts, type = None, fromDelta = None, toDelta = None, fromStrike = None, toStrike = None, fromPrice = None, toPrice = None, reverse = False):
      # Make sure all constraints are set
      fromDelta = fromDelta or 0
      fromStrike = fromStrike or 0
      fromPrice = fromPrice or 0
      toDelta = toDelta or 100
      toStrike = toStrike or float('inf')
      toPrice = toPrice or float('inf')

      # Sort the contracts by their strike in the specified order. Filter them by the specified criteria (Type/Delta/Strike/Price constrains)
      result = sorted([contract 
                        for contract in contracts 
                           if self.optionTypeFilter(contract, type)
                              # Delta constraint
                              and (fromDelta/100.0 <= abs(contract.BSMGreeks.Delta) <= toDelta/100.0)
                              # Strike constraint
                              and (fromStrike <= contract.Strike <= toStrike)
                              # Option price constraint (based on the mid-price)
                              and (fromPrice <= self.midPrice(contract) <= toPrice)
                     ]
                     , key = lambda x: x.Strike
                     , reverse = reverse
                     )
      # Return result
      return result   


   def getPuts(self, contracts, fromDelta = None, toDelta = None, fromStrike = None, toStrike = None, fromPrice = None, toPrice = None):

      # Sort the Put contracts by their strike in reverse order. Filter them by the specified criteria (Delta/Strike/Price constrains)
      return self.getContracts(contracts
                               , type = "Put"
                               , fromDelta = fromDelta
                               , toDelta = toDelta
                               , fromStrike = fromStrike
                               , toStrike = toStrike
                               , fromPrice = fromPrice
                               , toPrice = toPrice
                               , reverse = True
                               )


   def getCalls(self, contracts, fromDelta = None, toDelta = None, fromStrike = None, toStrike = None, fromPrice = None, toPrice = None):

      # Sort the Call contracts by their strike in ascending order. Filter them by the specified criteria (Delta/Strike/Price constrains)
      return self.getContracts(contracts
                               , type = "Call"
                               , fromDelta = fromDelta
                               , toDelta = toDelta
                               , fromStrike = fromStrike
                               , toStrike = toStrike
                               , fromPrice = fromPrice
                               , toPrice = toPrice
                               , reverse = False
                               )


   # Get the wing contract at the requested distance
   # Assumptions: 
   #  - The input contracts are sorted by increasing distance from the ATM (ascending order for Calls, descending order for Puts)
   #  - The first contract in the list is assumed to be one of the legs of the spread, and it is used used to determine the distance for the wing
   def getWing(self, contracts, wingSize = None):
      # Make sure the wingSize is specified
      wingSize = wingSize or 0

      # Initialize output
      wingContract = None

      if len(contracts) > 1 and wingSize > 0:
         # Get the short strike
         firstLegStrike = contracts[0].Strike
         # keep track of the wing size based on the long contract being selected
         currentWings = 0
         # Loop through all contracts
         for contract in contracts[1:]:
            # Select the long contract as long as it is within the specified wing size
            if abs(contract.Strike - firstLegStrike) <= wingSize:
               currentWings = abs(contract.Strike - firstLegStrike)
               wingContract = contract
            else:
               # We have exceeded the wing size, check if the distance to the requested wing size is closer than the contract previously selected
               if (abs(contract.Strike - firstLegStrike) - wingSize < wingSize - currentWings):
                  wingContract = contract
               break
         ### Loop through all contracts
      ### if wingSize > 0

      return wingContract


   # Get Spread contracts (Put or Call)
   def getSpread(self, contracts, type, strike = None, delta = None, wingSize = None, sortByStrike = False):
      # Type is a required parameter
      if type == None:
         self.logger.error(f"Input parameter type = {type} is invalid. Valid values: 'Put'|'Call'")
         return

      type = type.lower()
      if type == "put":
         # Get all Puts with a strike lower than the given strike and delta lower than the given delta
         sorted_contracts = self.getPuts(contracts, toDelta = delta, toStrike = strike)
      elif type == "call":
         # Get all Calls with a strike higher than the given strike and delta lower than the given delta
         sorted_contracts = self.getCalls(contracts, toDelta = delta, fromStrike = strike)
      else:
         self.logger.error(f"Input parameter type = {type} is invalid. Valid values: 'Put'|'Call'")
         return

      # Get the wing
      wing = self.getWing(sorted_contracts, wingSize = wingSize)
      # Initialize the result
      spread = []
      # Check if we have any contracts
      if(len(sorted_contracts) > 0):
         # Add the first leg
         spread.append(sorted_contracts[0])
         if wing != None:
            # Add the wing
            spread.append(wing)

      # By default, the legs of a spread are sorted based on their distance from the ATM strike.
      # - For Call spreads, they are already sorted by increasing strike
      # - For Put spreads, they are sorted by decreasing strike
      # In some cases it might be more convenient to return the legs ordersed by their strike (i.e. in case of Iron Condors/Flys)
      if sortByStrike:
         spread = sorted(spread, key = lambda x: x.Strike, reverse = False)

      return spread


   # Get Put Spread contracts
   def getPutSpread(self, contracts, strike = None, delta = None, wingSize = None, sortByStrike = False):
      return self.getSpread(contracts, "Put", strike = strike, delta = delta, wingSize = wingSize, sortByStrike = sortByStrike)


   # Get Put Spread contracts
   def getCallSpread(self, contracts, strike = None, delta = None, wingSize = None, sortByStrike = True):
      return self.getSpread(contracts, "Call", strike = strike, delta = delta, wingSize = wingSize, sortByStrike = sortByStrike)

