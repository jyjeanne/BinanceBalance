import Tkinter as tk
import ttk
import tkFileDialog
import pandas as pd
from binance.client import Client
from binance.websockets import BinanceSocketManager
from binance.enums import *
import numpy as np
from datetime import datetime
from tkinter import messagebox
import Queue
from twisted.internet import reactor
import os.path

def round_decimal(num, decimal):
    """
    Round a given floating point number 'num' to the nearest integer
    multiple of another floating point number 'decimal' smaller than
    'num' and return it as a string with up to 8 decimal places,
    dropping any trailing zeros.
    """
    if decimal > 0:
        x = np.round(num/decimal, 0)*decimal
    else:
        x = np.round(num, 8)
    return '{0:.8f}'.format(x).rstrip('0').rstrip('.')


class BalanceGUI(tk.Frame):
    def __init__(self, parent, coins):
        """ Initialize all GUI widgets """
        tk.Frame.__init__(self, parent)
        parent.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.parent = parent
        parent.deiconify()
        self.coins = coins
        self.coins_base = coins
        self.queue = Queue.Queue()
        self.trades_placed = 0
        self.trades_completed = 0
        self.trade_currency = 'BTC'
        self.trades = []
        self.headers = self.column_headers()
        coincount = len(coins)
        self.timer = 1000/(5*coincount)

        #portfolio display
        self.portfolio_view = tk.LabelFrame(parent, text='Portfolio')
        self.portfolio_view.grid(row=0,column=0, sticky=tk.E+tk.W+tk.N+tk.S)
        self.portfolio = ttk.Treeview(self.portfolio_view)
        self.portfolio['columns']=('Stored','Exchange', 'Target','Actual', 'Bid', 'Ask', 'Action', 'Status')
        for label in self.portfolio['columns']:
            if label == 'Status':
                self.portfolio.column(label, width=250)
            elif label == 'Action':
                self.portfolio.column(label, width=150)
            else:
                self.portfolio.column(label, width=100)
            self.portfolio.heading(label, text=label)
        self.portfolio.grid(row=0,column=0)

        #options display
        self.controls_view = tk.LabelFrame(parent, text='Controls')
        self.controls_view.grid(row=1, column=0, sticky=tk.E+tk.W)

        key_label = tk.Label(self.controls_view, text='API Key')
        key_label.grid(row=0, column=0,sticky=tk.E+tk.W)
        secret_label = tk.Label(self.controls_view, text='API Secret')
        secret_label.grid(row=0, column=2,sticky=tk.E+tk.W)
        self.key_entry = tk.Entry(self.controls_view, show='*')
        self.key_entry.grid(row=0, column=1,sticky=tk.E+tk.W)
        self.secret_entry = tk.Entry(self.controls_view, show='*')
        self.secret_entry.grid(row=0, column=3,sticky=tk.E+tk.W)
        self.login = tk.Button(self.controls_view, text='Login', command = self.api_enter)
        self.login.grid(row=0, column=4, sticky=tk.E+tk.W)

        

        self.ordertype = tk.StringVar()
        self.ordertype.set('Market')
        self.orderopt = tk.OptionMenu(self.controls_view, self.ordertype, 'Market', 'Market-Limit')
        self.orderopt.grid(row=1, column=0, stick=tk.E+tk.W)
        self.orderopt['state'] = 'disabled'

        self.dryrun_button = tk.Button(self.controls_view, text='Dry Run', command=self.dryrun, state='disabled')
        self.dryrun_button.grid(row=1,column=1, sticky=tk.E+tk.W)
        self.sell_button = tk.Button(self.controls_view, text='Execute Sells', command=self.execute_sells, state='disabled')
        self.sell_button.grid(row=1,column=2, sticky=tk.E+tk.W)
        self.buy_button = tk.Button(self.controls_view, text='Execute Buys', command=self.execute_buys, state='disabled')
        self.buy_button.grid(row=1,column=3, sticky=tk.E+tk.W)
        

    def on_closing(self):
        """ Check that all trades have executed before starting the save and exit process """
        if self.trades_placed > 0 and self.trades_completed < self.trades_placed:
            if messagebox.askokcancel('Quit', 'Not all trades have completed, some trade data might not be recorded. Quit anyway?'):
                self.save_and_quit()
        else:
            self.save_and_quit()

    def save_and_quit(self):
        """
        If trades have been executed in the current session, save them to file. Stop all websockets and exit the GUI.
        """
        if len(self.trades) > 0:
            df = pd.DataFrame(self.trades)
            if os.path.isfile('trade_history.csv'):
                with open('trade_history.csv','a') as f:
                    df.to_csv(f, sep=',', header=False, index=False)
            else:
                with open('trade_history.csv','w') as f:
                    df.to_csv(f, sep=',', header=True, index=False)
        try:
            self.bm.close()
            reactor.stop()
        except AttributeError:
            self.parent.destroy()
        else:
            self.parent.destroy()

    
    def api_enter(self):
        """ Log in to Binance with the provided credentials, update user portfolio and start listening to price and account update websockets. """
        api_key = self.key_entry.get()
        self.key_entry.delete(0,'end')
        api_secret = self.secret_entry.get()
        self.secret_entry.delete(0,'end')

        
        self.key_entry['state'] = 'disabled'
        self.secret_entry['state'] = 'disabled'
        self.login['state'] = 'disabled'
        self.dryrun_button['state'] = 'normal'
        self.orderopt['state'] = 'normal'

        self.client = Client(api_key, api_secret)
        status = self.client.get_system_status()
        
        self.populate_portfolio()
        self.start_websockets()

    def start_websockets(self):
        """ Start websockets to get price updates for all coins in the portfolio, trade execution reports, and user account balance updates. Start the message queue processor. """
        self.bm = BinanceSocketManager(self.client)
        self.bm.start()
        trade_currency = self.trade_currency
        symbols = self.coins['symbol'].tolist()
        symbols.remove(trade_currency+trade_currency)

        self.sockets = {}
        for symbol in symbols:
            self.sockets[symbol] = self.bm.start_symbol_ticker_socket(symbol, self.queue_msg)
        self.sockets['user'] = self.bm.start_user_socket(self.queue_msg)
        self.parent.after(10, self.process_queue)

    def populate_portfolio(self):
        """ Get all symbol info from Binance needed to populate user portfolio data and execute trades """
        self.coins = self.coins_base
        self.portfolio.delete(*self.portfolio.get_children())
        exchange_coins = []
        trade_currency = self.trade_currency
        self.trade_coin = trade_currency
        
        for coin in self.coins['coin']:
            pair = coin+trade_currency
            balance = self.client.get_asset_balance(asset=coin)
            if coin != trade_currency:
                price = float(self.client.get_symbol_ticker(symbol = pair)['price'])
                symbolinfo = self.client.get_symbol_info(symbol=pair)['filters']
                row = {'coin': coin, 'exchange_balance': float(balance['free']),
                   'minprice': float(symbolinfo[0]['minPrice']), 'maxprice': float(symbolinfo[0]['maxPrice']), 'ticksize': float(symbolinfo[0]['tickSize']),
                   'minqty': float(symbolinfo[1]['minQty']), 'maxqty': float(symbolinfo[1]['maxQty']), 'stepsize': float(symbolinfo[1]['stepSize']),                   
                   'minnotional': float(symbolinfo[2]['minNotional']), 'symbol': pair, 'askprice' : price, 'bidprice': price, 'price': price}
            else:
                fixed_balance = self.coins.loc[self.coins['coin'] == coin]['fixed_balance']
                row = {'coin': coin, 'exchange_balance': float(balance['free']),
                   'minprice': 0, 'maxprice': 0, 'ticksize': 0,
                   'minqty': 0, 'maxqty': 0, 'stepsize': 0,                   
                   'minnotional': 0, 'symbol': coin+coin, 'askprice' : 1.0, 'bidprice': 1.0, 'price': 1.0}
            exchange_coins.append(row)
        exchange_coins = pd.DataFrame(exchange_coins)
        self.coins = pd.merge(self.coins, exchange_coins, on='coin', how='outer')

        self.coins['value'] = self.coins.apply(lambda row: row.price*(row.exchange_balance + row.fixed_balance), axis=1)
        self.total = np.sum(self.coins['value'])
        self.coins['actual'] = self.coins.apply(lambda row: 100.0*row.value/self.total, axis=1)
        
        i = 0
        for row in self.coins.itertuples():
            self.portfolio.insert("" , i, iid=row.coin, text=row.coin,
                                  values=(round_decimal(row.fixed_balance, row.stepsize), round_decimal(row.exchange_balance, row.stepsize),
                                          '{0} %'.format(row.allocation), '{0:.2f} %'.format(row.actual), round_decimal(row.price, row.ticksize),round_decimal(row.price, row.ticksize),'','Waiting'))
            i += 1

    def queue_msg(self, msg):
        """ Whenever a weboscket receives a message, check for errors. If an error occurs, restart websockets. If no error, add it to the message queue. """
        if msg['e'] == 'error':
            self.bm.close()
            reactor.stop()
            self.start_websockets()
        else:
            self.queue.put(msg)
        
    def process_queue(self):
        """ Check for new messages in the queue periodically, and reroute them to the appropriate handler. Recursively calls itself to perpetuate the process. """
        try:
            msg = self.queue.get(0)
        except Queue.Empty:
            pass
        else:
            if msg['e'] == '24hrTicker':
                self.update_price(msg)
            elif msg['e'] == 'outboundAccountInfo':
                self.update_balance(msg)
            elif msg['e'] == 'executionReport':
                self.update_trades(msg)
        print self.queue.qsize()
        self.master.after(self.timer, self.process_queue)

    def update_trades(msg):
        """ Update balances whenever a partial execution occurs """
        coin = msg['s'][:-len(self.trade_coin)]
        savemsg = {self.headers[key] : value for key, value in msg.items()}
        percent = 100.0*float(savemsg['cumulative_filled_quantity'])/float(savemsg['order_quantity'])
        if percent < 100.0:
            self.portfolio.set(coin, column='Status', value = 'In Progress: {0:.2f}%'.format(percent))
        else:
            self.trades_completed += 1
            self.portfolio.set(coin, column='Status', value = 'Completed')
        self.trades.append(savemsg)    


    def update_balance(self, msg):
        """ Update user balances internally and on the display whenever an account update message is received. """
        balances = msg['B']
        for balance in balances:
            coin = balance['a']
            exchange_balance = balance['f'] + balance['l']
            self.portfolio.set(coin, column='Exchange', value=round_decimal(exchange_balance,self.coins.loc[self.coins['coin'] == coin, 'stepsize'].values[0]))
            self.coins.loc[self.coins['coin'] == coin, 'exchange_balance'] = exchange_balance
            ask = self.coins.loc[self.coins['coin'] == coin, 'askprice'].values[0]
            value = (self.coins.loc[self.coins['coin'] == coin, 'exchange_balance'].values[0] + self.coins.loc[self.coins['coin'] == coin, 'fixed_balance'].values[0])*ask
            self.coins.loc[self.coins['coin'] == coin, 'value'] = value

        self.total = np.sum(self.coins['value']) 
        self.coins['actual'] = self.coins.apply(lambda row: 100.0*row.value/self.total, axis=1)
        for row in self.coins.itertuples():
            coin = row.coin
            self.portfolio.set(coin, column='Actual', value='{0:.2f}%'.format(self.coins.loc[self.coins['coin'] == coin, 'actual'].values[0]))

    def update_price(self, msg):
        """ Update symbol prices and user allocations internally and on the display whenever a price update is received. """
        coin = msg['s'][:-len(self.trade_currency)]
        ask = float(msg['a'])
        bid = float(msg['b'])
        
        self.portfolio.set(coin, column='Ask', value=round_decimal(ask,self.coins.loc[self.coins['coin'] == coin, 'ticksize'].values[0]))
        self.coins.loc[self.coins['coin'] == coin, 'askprice'] = ask
        
        self.portfolio.set(coin, column='Bid', value=round_decimal(bid,self.coins.loc[self.coins['coin'] == coin, 'ticksize'].values[0]))
        self.coins.loc[self.coins['coin'] == coin, 'bidprice'] = bid
        
        value = (self.coins.loc[self.coins['coin'] == coin, 'exchange_balance'].values[0] + self.coins.loc[self.coins['coin'] == coin, 'fixed_balance'].values[0])*ask
        self.coins.loc[self.coins['coin'] == coin, 'value'] = value

        self.total = np.sum(self.coins['value'])
        
        self.coins['actual'] = self.coins.apply(lambda row: 100.0*row.value/self.total, axis=1)

        for row in self.coins.itertuples():
            coin = row.coin
            self.portfolio.set(coin, column='Actual', value='{0:.2f}%'.format(self.coins.loc[self.coins['coin'] == coin, 'actual'].values[0]))
        
                          
    def dryrun(self):
        """ Determine approximate trades which must occur in order to rebalance, and report them to the user. Place test orders to ensure compliance with Binance API """
        self.sell_button['state'] = 'normal'
        self.coins['difference'] = self.coins.apply(lambda row: (row.allocation - row.actual)/100.0 * self.total/row.price,axis=1)
        for row in self.coins.itertuples():
            status = ''
            coin = row.coin
            pair = coin+self.trade_coin
            balance = row.exchange_balance
            actual = row.actual
            dif = row.difference
            qty = np.absolute(dif)
            if dif < 0:
                side = SIDE_SELL
                price = row.bidprice
            else:
                side = SIDE_BUY
                price = row.askprice
            if side == SIDE_SELL and qty > balance and coin != self.trade_coin:
                status = 'Insufficient funds for complete rebalance'
            action = 'None'
            if coin == self.trade_coin:
                action = 'Ready'
            elif qty < row.minqty:
                action = 'Trade quantity too small'
            elif qty > row.maxqty:
                action = 'Trade quantity too large'
            elif qty * price < row.minnotional:
                action = 'Trade value too small'
            else:
                action = '{0} {1}'.format(side, round_decimal(qty, row.stepsize))
                
                trade_type = self.ordertype.get()
                trade_currency = self.trade_coin
                try:
                    if trade_type == 'Market-Limit':
                        order = self.client.create_test_order(symbol = pair,
                                                             side = side,
                                                             type = ORDER_TYPE_LIMIT,
                                                             timeInForce = TIME_IN_FORCE_GTC,
                                                             quantity = round_decimal(qty, row.stepsize),
                                                             price = round_decimal(price, row.ticksize))
                    elif trade_type == 'Market':
                        order = self.client.create_test_order(symbol = pair,
                                                             side = side,
                                                             type = ORDER_TYPE_MARKET,
                                                             quantity = round_decimal(qty, row.stepsize))                    
                except Exception as e:
                    status = e
            self.portfolio.set(coin, column='Status', value=status)
            self.portfolio.set(coin, column='Action', value=action)

        
    def execute_sells(self):
        """ Execute any sell orders required to rebalance the portfolio, and enable buy orders """
        self.sell_button['state'] = 'disabled'
        self.buy_button['state'] = 'normal'
        self.coins['difference'] = self.coins.apply(lambda row: (row.allocation - row.actual)/100.0 * self.total/row.price,axis=1)
        sellcoins = self.coins[self.coins['difference'] < 0]
        for row in sellcoins.itertuples():
            coin = row.coin
            balance = row.exchange_balance
            
            pair = coin+self.trade_coin
            actual = row.actual
            dif = row.difference
            qty = np.absolute(dif)
            side = SIDE_SELL
            price = row.bidprice
            if qty > balance:
                qty = balance
            action = 'None'
            if coin == self.trade_coin:
                action = 'Ready'
            elif qty < row.minqty:
                action = 'Trade quantity too small'
            elif qty > row.maxqty:
                action = 'Trade quantity too large'
            elif qty * price < row.minnotional:
                action = 'Trade value too small'
            else:
                action = '{0} {1}'.format(side, round_decimal(qty, row.stepsize))
                
                trade_type = self.ordertype.get()
                trade_currency = self.trade_coin
                try:
                    if trade_type == 'Market-Limit':
                        order = self.client.create_order(symbol = pair,
                                                             side = side,
                                                             type = ORDER_TYPE_LIMIT,
                                                             timeInForce = TIME_IN_FORCE_GTC,
                                                             quantity = round_decimal(qty, row.stepsize),
                                                             price = round_decimal(price, row.ticksize))
                    elif trade_type == 'Market':
                        order = self.client.create_order(symbol = pair,
                                                             side = side,
                                                             type = ORDER_TYPE_MARKET,
                                                             quantity = round_decimal(qty, row.stepsize))                    
                except Exception as e:
                    self.portfolio.set(coin, column='Status', value=e)
                else:
                    self.trades_placed += 1
                    self.portfolio.set(coin, column='Status', value='Trade Placed')
            self.portfolio.set(coin, column='Action', value=action)

    def execute_buys(self):
        """ Execute any sell orders required to rebalance the portfolio """
        self.buy_button['state'] = 'disabled'
        self.coins['difference'] = self.coins.apply(lambda row: (row.allocation - row.actual)/100.0 * self.total/row.price,axis=1)
        buycoins = self.coins[self.coins['difference'] > 0]
        for row in buycoins.itertuples():
            coin = row.coin
            balance = row.exchange_balance
            
            pair = coin+self.trade_coin
            actual = row.actual
            dif = row.difference
            qty = np.absolute(dif)
            side = SIDE_BUY
            price = row.askprice
            action = 'None'
            if coin == self.trade_coin:
                action = 'Ready'
            elif qty < row.minqty:
                action = 'Trade quantity too small'
            elif qty > row.maxqty:
                action = 'Trade quantity too large'
            elif qty * price < row.minnotional:
                action = 'Trade value too small'
            else:
                action = '{0} {1}'.format(side, round_decimal(qty, row.stepsize))
                
                trade_type = self.ordertype.get()
                trade_currency = self.trade_coin
                try:
                    if trade_type == 'Market-Limit':
                        order = self.client.create_order(symbol = pair,
                                                             side = side,
                                                             type = ORDER_TYPE_LIMIT,
                                                             timeInForce = TIME_IN_FORCE_GTC,
                                                             quantity = round_decimal(qty, row.stepsize),
                                                             price = round_decimal(price, row.ticksize))
                    elif trade_type == 'Market':
                        order = self.client.create_order(symbol = pair,
                                                             side = side,
                                                             type = ORDER_TYPE_MARKET,
                                                             quantity = round_decimal(qty, row.stepsize))                    
                except Exception as e:
                    self.portfolio.set(coin, column='Status', value=e)
                else:
                    self.trades_placed += 1
                    self.portfolio.set(coin, column='Status', value='Trade Placed')
            self.portfolio.set(coin, column='Action', value=action)
            
   
    def column_headers(self):
        """ define human readable aliases for the headers in trade execution reports. """
        return {'e': 'event_type',
                'E': 'event_time',
                's': 'symbol',
                'c': 'client_order_id',
                'S': 'side',
                'o': 'type',
                'f': 'time_in_force',
                'q': 'order_quantity',
                'p': 'order_price',
                'F': 'iceberg_quantity',
                'g': 'ignore_1',
                'C': 'original_client_order_id',
                'x': 'current_execution_type',
                'X': 'current_order_status',
                'r': 'order_reject_reason',
                'i': 'order_id',
                'l': 'last_executed_quantity',
                'z': 'cumulative_filled_quantity',
                'L': 'last_executed_price',
                'n': 'commission_amount',
                'N': 'commission_asset',
                'T': 'transction_time',
                't': 'trade_id',
                'I': 'ignore_2',
                'w': 'order_working',
                'm': 'maker_side',
                'M': 'ignore_3'}
    
def main():
    root = tk.Tk()
    root.withdraw()
    portfolio = 'allocation.csv' #tkFileDialog.askopenfilename(initialdir='C:/Users/kbrig035/Documents/GitHub/BinanceBalance/')
    coins = pd.read_csv(portfolio)
    BalanceGUI(root, coins).grid(row=0, column=0)
    root.wm_title('BinanceBalance')
    root.mainloop()

if __name__=="__main__":
    main()
