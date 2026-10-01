# Notely Reader

O Reader abre o PDF localmente para resposta visual imediata e envia o arquivo à API. A persistência e as anotações só são habilitadas depois que o backend conclui o scan do ClamAV e o armazenamento no MinIO.

## Executar

Com backend, PostgreSQL, MinIO e ClamAV disponíveis:

```bash
cd frontend
npm install
npm run dev
```

Abra `http://localhost:5173`. O Vite encaminha `/api` para `http://localhost:8000` durante o desenvolvimento.

## Testar e compilar

```bash
npm test
npm run build
```

## Escopo atual

- renderização local via PDF.js;
- upload multipart verificado;
- armazenamento MinIO por SHA-256;
- posições normalizadas independentes do zoom;
- highlights, notas, dúvidas, itens importantes e discordâncias restaurados pela API.

A biblioteca de documentos e o download autenticado do PDF armazenado ficam para o próximo corte.
