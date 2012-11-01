import sys
sys.path.insert(0, '/home/ubuntu/minitwit')
activate_this = '/home/ubuntu/twit/bin/activate_this.py'
execfile(activate_this, dict(__file__=activate_this))
from minitwit import app as application
