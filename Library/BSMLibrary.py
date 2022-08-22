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
from math import *
from scipy import optimize
from scipy.stats import norm
from Logger import *
from ContractUtils import *

class BSM:

   def __init__(self, context, tradingDays = 365.0):
      # Set the context
      self.context = context
      # Set the logger
      self.logger = Logger(context, className = type(self).__name__, logLevel = context.logLevel)
      # Initialize the contract utils
      self.contractUtils = ContractUtils(context)
      # Set the IR 
      self.riskFreeRate = context.riskFreeRate
      # Set the number of trading days
      self.tradingDays = tradingDays
      
   def isITM(self, contract, spotPrice = None):
      # Get the current price of the underlying unless otherwise specified
      if spotPrice == None:
         spotPrice = self.contractUtils.getUnderlyingLastPrice(contract)
   
      if contract.Right == OptionRight.Call:
         # A Call option is in the money if the underlying price is above the strike price
         return contract.Strike < spotPrice
      else:
         # A Put option is in the money if the underlying price is below the strike price
         return spotPrice < contract.Strike
         
   def bsmD1(self, contract, sigma, tau = None, ir = None, spotPrice = None, atTime = None):
      # Get the DTE as a fraction of a year
      if tau == None:
         tau = self.optionTau(contract, atTime = atTime)

      # Use the risk free rate unless otherwise specified
      if ir == None:
         ir = self.riskFreeRate

      # Get the current price of the underlying unless otherwise specified
      if spotPrice == None:
         spotPrice = self.contractUtils.getUnderlyingLastPrice(contract)

      # Strike price
      strikePrice = contract.Strike
      
      # Check edge cases:
      #  - The contract is expired -> tau = 0
      #  - The IV could not be computed (deep ITM or far OTM options) -> sigma = 0
      if tau == 0 or sigma == 0:
         # Set the sign based on whether it is a Call (+1) or a Put (-1)
         sign = 2*int(contract.Right == OptionRight.Call)-1
         if(self.isITM(contract, spotPrice = spotPrice)):
            # Deep ITM options:
            #  - Call: d1 = Inf -> Delta = Norm.CDF(d1) = 1
            #  - Put: d1 = -Inf -> Delta = -Norm.CDF(-d1) = -1
            d1 = sign * float('inf')
         else:
            # Far OTM options:
            #  - Call: d1 = -Inf -> Delta = Norm.CDF(d1) = 0
            #  - Put: d1 = Inf -> Delta = -Norm.CDF(-d1) = 0
            d1 = sign * float('-inf')
      else:
         d1 = (np.log(spotPrice/strikePrice) + (ir + 0.5*sigma**2)*tau)/(sigma * np.sqrt(tau))
      return d1


      
   def bsmD2(self, contract, sigma, tau = None, d1 = None, ir = None, spotPrice = None, atTime = None):

      # Get the DTE as a fraction of a year
      if tau == None:
         tau = self.optionTau(contract, atTime = atTime)

      if d1 == None:
         d1 = self.bsmD1(contract, sigma, tau = tau, ir = ir, spotPrice = spotPrice)
   
      # Compute D2
      d2 = d1 - sigma * np.sqrt(tau)
      return d2
   
   # Compute the DTE as a time fraction of the year
   def optionTau(self, contract, atTime = None):
      if atTime == None:
         atTime = self.context.Time
      # Get the expiration date and add 16 hours to the market close
      expiryDttm = contract.Expiry + timedelta(hours = 16)
      # Time until market close
      timeDiff = expiryDttm - atTime
      # Days to expiration: use the fraction of minutes until market close in case of 0-DTE (390 minutes = 6.5h -> from 9:30 to 16:00)
      dte = max(0, timeDiff.days, timeDiff.seconds/(60.0*390.0))
      # DTE as a fraction of a year
      tau = dte/self.tradingDays
      return tau

   # Pricing of a European option based on the Black Scholes Merton model (without dividends)
   def bsmPrice(self, contract, sigma, tau = None, ir = None, spotPrice = None, atTime = None):
      
      # Get the DTE as a fraction of a year
      if tau == None:
         tau = self.optionTau(contract, atTime = atTime)
      
      # Get the current price of the underlying unless otherwise specified
      if spotPrice == None:
         spotPrice = self.contractUtils.getUnderlyingLastPrice(contract)
      # Compute D1
      d1 = self.bsmD1(contract, sigma, tau = tau, ir = ir, spotPrice = spotPrice)
      # Compute D2
      d2 = self.bsmD2(contract, sigma, tau = tau, d1 = d1, ir = ir, spotPrice = spotPrice)
      # X*e^(-r*tau)
      Xert = contract.Strike * np.exp(-self.riskFreeRate*tau)

      #Price the option
      if contract.Right == OptionRight.Call:
         # Call Option
         theoreticalPrice = norm.cdf(d1)*spotPrice - norm.cdf(d2)*Xert
      else:
         # Put Option
         theoreticalPrice = norm.cdf(-d2)*Xert - norm.cdf(-d1)*spotPrice
      return theoreticalPrice



   # Compute the Theta of an option
   def bsmTheta(self, contract, sigma, tau = None, d1 = None, d2 = None, ir = None, spotPrice = None, atTime = None):
      # Get the DTE as a fraction of a year
      if tau == None:
         tau = self.optionTau(contract, atTime = atTime)
      # Get the current price of the underlying unless otherwise specified
      if spotPrice == None:
         spotPrice = self.contractUtils.getUnderlyingLastPrice(contract)
      # Compute D1
      if d1 == None:
         d1 = self.bsmD1(contract, sigma, tau = tau, ir = ir, spotPrice = spotPrice)
      # Compute D2
      if d2 == None:
         d2 = self.bsmD2(contract, sigma, tau = tau, d1 = d1, ir = ir, spotPrice = spotPrice)
      # -S*N'(d1)*sigma/(2*sqrt(tau))
      SNs = -(spotPrice * norm.pdf(d1) * sigma) / (2.0 * np.sqrt(tau))
      # r*X*e^(-r*tau)
      rXert = self.riskFreeRate * contract.Strike * np.exp(-self.riskFreeRate*tau)
      # Compute Theta (divide by the number of trading days to get a daily Theta value)
      if contract.Right == OptionRight.Call:
         theta = (SNs  -  rXert * norm.cdf(d2))/self.tradingDays
      else:
         theta = (SNs  +  rXert * norm.cdf(-d2))/self.tradingDays
      return theta


   # Compute the Theta of an option
   def bsmRho(self, contract, sigma, tau = None, d1 = None, d2 = None, ir = None, spotPrice = None, atTime = None):
      # Get the DTE as a fraction of a year
      if tau == None:
         tau = self.optionTau(contract, atTime = atTime)
      # Get the current price of the underlying unless otherwise specified
      if spotPrice == None:
         spotPrice = self.contractUtils.getUnderlyingLastPrice(contract)
      # Compute D1
      if d1 == None:
         d1 = self.bsmD1(contract, sigma, tau = tau, ir = ir, spotPrice = spotPrice)
      # Compute D2
      if d2 == None:
         d2 = self.bsmD2(contract, sigma, tau = tau, d1 = d1, ir = ir, spotPrice = spotPrice)
      # tau*X*e^(-r*tau)
      tXert = tau * self.riskFreeRate * contract.Strike * np.exp(-self.riskFreeRate*tau)
      # Compute Theta
      if contract.Right == OptionRight.Call:
         rho = tXert * norm.cdf(d2)
      else:
         rho = -tXert * norm.cdf(-d2)
      return rho


   # Compute the Gamma of an option
   def bsmGamma(self, contract, sigma, tau = None, d1 = None, ir = None, spotPrice = None, atTime = None):
      # Get the DTE as a fraction of a year
      if tau == None:
         tau = self.optionTau(contract, atTime = atTime)
      # Get the current price of the underlying unless otherwise specified
      if spotPrice == None:
         spotPrice = self.contractUtils.getUnderlyingLastPrice(contract)
      # Compute D1
      if d1 == None:
         d1 = self.bsmD1(contract, sigma, tau = tau, ir = ir, spotPrice = spotPrice)
      # Compute Gamma
      if(sigma == 0 or tau == 0):
         gamma = float('inf')
      else:
         gamma = norm.pdf(d1) / (spotPrice * sigma * np.sqrt(tau))
      return gamma


   
   # Compute the Vega of an option
   def bsmVega(self, contract, sigma, tau = None, d1 = None, ir = None, spotPrice = None, atTime = None):
      # Get the DTE as a fraction of a year
      if tau == None:
         tau = self.optionTau(contract, atTime = atTime)
      # Get the current price of the underlying unless otherwise specified
      if spotPrice == None:
         spotPrice = self.contractUtils.getUnderlyingLastPrice(contract)
      # Compute D1
      if d1 == None:
         d1 = self.bsmD1(contract, sigma, tau = tau, ir = ir, spotPrice = spotPrice)
      # Compute Vega
      vega = spotPrice * norm.pdf(d1) * np.sqrt(tau)
      return vega


   # Compute the Vomma of an option
   def bsmVomma(self, contract, sigma, tau = None, d1 = None, d2 = None, ir = None, spotPrice = None, atTime = None):
      # Get the DTE as a fraction of a year
      if tau == None:
         tau = self.optionTau(contract, atTime = atTime)
      # Get the current price of the underlying unless otherwise specified
      if spotPrice == None:
         spotPrice = self.contractUtils.getUnderlyingLastPrice(contract)
      # Compute D1
      if d1 == None:
         d1 = self.bsmD1(contract, sigma, tau = tau, ir = ir, spotPrice = spotPrice)
      # Compute D2
      if d2 == None:
         d2 = self.bsmD2(contract, sigma, tau = tau, d1 = d1, ir = ir, spotPrice = spotPrice)
      # Compute Vomma
      if(sigma == 0):
         vomma = float('inf')
      else:
         vomma = spotPrice * norm.pdf(d1) * np.sqrt(tau) * d1 * d2 / sigma
      return vomma
   
   # Compute Implied Volatility from the price of an option
   def bsmIV(self, contract, tau = None, saveIt = False):
   
      # Start the timer
      self.context.executionTimer.start()
   
      # Inner function used to compute the root
      def f(sigma, contract, tau):
         return self.bsmPrice(contract, sigma = sigma, tau = tau) - self.contractUtils.midPrice(contract)
      # First order derivative  (Vega)  
      def fprime(sigma, contract, tau):
         return self.bsmVega(contract, sigma = sigma, tau = tau)
      # Second order derivative (Vomma)    
      def fprime2(sigma, contract, tau):
         return self.bsmVomma(contract, sigma = sigma, tau = tau)

      # Initialize the IV to zero in case anything goes wrong
      IV = 0
      # Initialize the flag to mark whether we were able to find the root
      converged = False
      
      # Find the root -> Implied Volatility: Use Halley's method
      try:
         # Start the search at the lastest known value for the IV (if previously calculated)
         x0 = 0.1
         if hasattr(contract, "BSMImpliedVolatility"):
            x0 = contract.BSMImpliedVolatility
         sol = optimize.root_scalar(f, x0 = x0, args = (contract, tau), fprime = fprime, fprime2 = fprime2, method = 'halley', xtol = 1e-6)
         # Get the convergence status
         converged = sol.converged
         # Set the IV if we found the root
         if converged:
            IV = sol.root
      except:
         pass

      # Fallback method (Bisection) if Halley's optimization failed
      if not converged:
         # Find the root -> Implied Volatility
         try:
            sol = optimize.root_scalar(f, bracket = [0.0001, 2], args = (contract, tau), xtol = 1e-6)
            # Get the convergence status
            converged = sol.converged
            # Set the IV if we found the root
            if converged:
               IV = sol.root
         except:
            pass

      
      # Check if we need to save the IV as an attribute of the contract object
      if saveIt:
         contract.BSMImpliedVolatility = IV
         
      # Stop the timer
      self.context.executionTimer.stop()
         
      # Return the result
      return IV
   
   # Compute the Delta of an option
   def bsmDelta(self, contract, sigma, tau = None, d1 = None, ir = None, spotPrice = None, atTime = None):
      if d1 == None:
         if tau == None:
            # Get the DTE as a fraction of a year
            tau = self.optionTau(contract, atTime = atTime)
                     
         # Compute D1
         d1 = self.bsmD1(contract, sigma, tau = tau, ir = ir, spotPrice = spotPrice)
      ### if (d1 == None)
         
      # Compute option delta (rounded to 2 digits)
      if contract.Right == OptionRight.Call:
         delta = norm.cdf(d1)
      else:
         delta = -norm.cdf(-d1)
      return delta
   
   def computeGreeks(self, contract, sigma = None, ir = None, spotPrice = None, atTime = None, saveIt = False):
      # Start the timer
      self.context.executionTimer.start()
      
      # Avoid recomputing the Greeks if we have already done it for this time bar
      if hasattr(contract, "BSMGreeks") and contract.BSMGreeks.lastUpdated == self.context.Time:
         return contract.BSMGreeks
      
      # Get the DTE as a fraction of a year
      tau = self.optionTau(contract, atTime = atTime)
      
      if sigma == None:
         # Compute Implied Volatility
         sigma = self.bsmIV(contract, tau = tau, saveIt = saveIt)
      ### if (sigma == None)
      
      # Get the current price of the underlying unless otherwise specified
      if spotPrice == None:
         spotPrice = self.contractUtils.getUnderlyingLastPrice(contract)
         
      # Compute D1
      d1 = self.bsmD1(contract, sigma, tau = tau, ir = ir, spotPrice = spotPrice)
      # Compute D2
      d2 = self.bsmD2(contract, sigma, tau = tau, d1 = d1, ir = ir, spotPrice = spotPrice)
            
      # First order derivatives
      delta = self.bsmDelta(contract, sigma = sigma, tau = tau, d1 = d1, ir = ir, spotPrice = spotPrice)
      theta = self.bsmTheta(contract, sigma, tau = tau, d1 = d1, d2 = d2, ir = ir, spotPrice = spotPrice)
      vega = self.bsmVega(contract, sigma, tau = tau, d1 = d1, ir = ir, spotPrice = spotPrice)
      rho = self.bsmRho(contract, sigma, tau = tau, d1 = d1, d2 = d2, ir = ir, spotPrice = spotPrice)

      # Second Order derivatives
      gamma = self.bsmGamma(contract, sigma, tau = tau, d1 = d1, ir = ir, spotPrice = spotPrice)
      vomma = self.bsmVomma(contract, sigma, tau = tau, d1 = d1, d2 = d2, ir = ir, spotPrice = spotPrice)
      
      # Lambda (a.k.a. elasticity or leverage: the percentage change in option value per percentage change in the underlying price)
      elasticity = delta * np.float64(spotPrice)/np.float64(self.contractUtils.midPrice(contract))
      
      
      # Create a Greeks object
      greeks = BSMGreeks(delta = delta
                         , gamma = gamma
                         , vega = vega
                         , theta = theta
                         , rho = rho
                         , vomma = vomma
                         , elasticity = elasticity
                         , IV = sigma
                         , lastUpdated = self.context.Time
                         )
      
      # Check if we need to save the Greeks as an attribute of the contract object
      if saveIt:
         contract.BSMGreeks = greeks

      # Stop the timer
      self.context.executionTimer.stop()
   
      return greeks
   
   
   # Compute and store the Greeks for a list of contracts
   def setGreeks(self, contracts, sigma = None, ir = None):
      # Start the timer
      self.context.executionTimer.start()

      if isinstance(contracts, list):
         # Loop through all contracts
         for contract in contracts:
            # Get the current price of the underlying
            spotPrice = self.contractUtils.getUnderlyingLastPrice(contract)
            # Compute the Greeks for the contract
            self.computeGreeks(contract, sigma = sigma, ir = ir, spotPrice = spotPrice, saveIt = True)
      else:
         # Get the current price of the underlying
         spotPrice = self.contractUtils.getUnderlyingLastPrice(contracts)
         # Compute the Greeks on a single contract
         self.computeGreeks(contracts, sigma = sigma, ir = ir, spotPrice = spotPrice, saveIt = True)
         
         # Log the contract details
         self.logger.trace(f"Contract: {contracts.Symbol}")
         self.logger.trace(f"  -> Contract Mid-Price: {self.contractUtils.midPrice(contracts)}")
         self.logger.trace(f"  -> Spot: {spotPrice}")
         self.logger.trace(f"  -> Strike: {contracts.Strike}")
         self.logger.trace(f"  -> Type: {'Call' if contracts.Right == OptionRight.Call else 'Put'}")
         self.logger.trace(f"  -> IV: {contracts.BSMImpliedVolatility}")
         self.logger.trace(f"  -> Delta: {contracts.BSMGreeks.Delta}")
         self.logger.trace(f"  -> Gamma: {contracts.BSMGreeks.Gamma}")
         self.logger.trace(f"  -> Vega: {contracts.BSMGreeks.Vega}")
         self.logger.trace(f"  -> Theta: {contracts.BSMGreeks.Theta}")
         self.logger.trace(f"  -> Rho: {contracts.BSMGreeks.Rho}")
         self.logger.trace(f"  -> Vomma: {contracts.BSMGreeks.Vomma}")
         self.logger.trace(f"  -> Elasticity: {contracts.BSMGreeks.Elasticity}")

      # Stop the timer
      self.context.executionTimer.stop()
      
      return
   

class BSMGreeks:
   def __init__(self, delta = None, gamma = None, vega = None, theta = None, rho = None, vomma = None, elasticity = None, IV = None, lastUpdated = None, precision = 5):
      self.Delta = self.roundIt(delta, precision)
      self.Gamma = self.roundIt(gamma, precision)
      self.Vega = self.roundIt(vega, precision)
      self.Theta = self.roundIt(theta, precision)
      self.Rho = self.roundIt(rho, precision)
      self.Vomma = self.roundIt(vomma, precision)
      self.Elasticity = self.roundIt(elasticity, precision)
      self.IV = self.roundIt(IV, precision)
      self.lastUpdated = lastUpdated
      
   def roundIt(self, value, precision = None):
      if precision:
         return round(value, precision)
      else:
         return value