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

from OptionStrategy import *

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
      