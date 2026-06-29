import requests, io, pandas as pd

URL = 'https://www.hfbrasil.org.br/br/estatistica/preco/exportar.aspx?produto=9&regiao[]=111&regiao[]=110&regiao[]=109&regiao[]=112&regiao[]=113&periodicidade=diario&ano_inicial=2023&ano_final=2026'
r = requests.get(URL, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.hfbrasil.org.br/'}, timeout=60)
df = pd.read_excel(io.BytesIO(r.content), header=None)
df.columns = df.iloc[0]
df = df.iloc[1:].reset_index(drop=True)
print('Colunas:', list(df.columns))
print('Produtos únicos:', df['Produto'].unique()[:10])
df_limao = df[df['Produto'].astype(str).str.contains('im', case=False, na=False)]
print('Linhas limão:', len(df_limao))
print(df_limao.head(3).to_string())
