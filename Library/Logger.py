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

class Logger:
   def __init__(self, context, className = None, logLevel = 0):
      if logLevel == None:
         logLevel = 0
         
      self.context = context
      self.className = className
      self.logLevel = logLevel

      
   def Log(self, msg, trsh = 0):
      # Set the class name (if available)
      if self.className != None:
         className = f"{self.className}."
            
      # Set the prefix for the message
      if trsh == None or trsh <= 0:
         prefix = "ERROR"
      elif trsh == 1:
         prefix = "WARNING"
      elif trsh == 2:
         prefix = "INFO"
      elif trsh == 3:
         prefix = "DEBUG"
      else:
         prefix = "TRACE"
            
      if self.logLevel >= trsh:
         self.context.Log(f" {prefix} -> {className}{sys._getframe(2).f_code.co_name}: {msg}")
      
   def error(self, msg):
      self.Log(msg, trsh = 0)

   def warning(self, msg):
      self.Log(msg, trsh = 1)
         
   def info(self, msg):
      self.Log(msg, trsh = 2)
         
   def debug(self, msg):
      self.Log(msg, trsh = 3)

   def trace(self, msg):
      self.Log(msg, trsh = 4)
