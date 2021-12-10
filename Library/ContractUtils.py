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

class ContractUtils:
   def __init__(self, context):
      # Set the context
      self.context = context
      # Set the logger
      self.logger = Logger(context, className = type(self).__name__, logLevel = context.logLevel)

   def getUnderlyingLastPrice(self, contract):
      # Get the context
      context = self.context
      # Get the object from the Securities dictionary if available (pull the latest price), else use the contract object itself
      if contract.UnderlyingSymbol in context.Securities:
         security = context.Securities[contract.UnderlyingSymbol]
         
      # Check if we have found the security
      if security != None:
         # Get the last known price of the security
         return context.GetLastKnownPrice(security).Price
      else:
         # Get the UnderlyingLastPrice attribute of the contract
         return contract.UnderlyingLastPrice
     

   def getSecurity(self, contract):
      # Get the Securities object
      Securities = self.context.Securities
      # Check if we can extract the Symbol attribute
      if hasattr(contract, "Symbol") and contract.Symbol in Securities:
         # Get the security from the Securities dictionary if available (pull the latest price), else use the contract object itself
         security = Securities[contract.Symbol]
      else:
         # Use the contract itself
         security = contract
      return security
   
   # Returns the mid-price of an option contract
   def midPrice(self, contract):
      security = self.getSecurity(contract)
      return 0.5*(security.BidPrice + security.AskPrice)

   def bidAskSpread(self, contract):
      security = self.getSecurity(contract)
      return abs(security.AskPrice - security.BidPrice)
