import requests
r = requests.get('https://api.hgbrasil.com/weather?woeid=433580&key=1ea8c99a', timeout=15)
d = r.json()['results']
print('temp:', d['temp'], '| rain:', d['rain'])
print('forecast[0]:', d['forecast'][0])
