// Private preview ingress. Only the fixed development targets are reachable.
import http from 'node:http';
import net from 'node:net';
const config = JSON.parse(process.env.NAS_PREVIEW_PROXY);
function target(path) {
  if (path === '/graphql' || path.startsWith('/graphql?')) return [config.api, path];
  if (path.startsWith('/supabase/')) return [config.supabase, path.slice(9)];
  if (path === '/mail') return [config.mail, '/'];
  if (path.startsWith('/mail/')) return [config.mail, path];
  return [config.dashboard, path];
}
function headers(req) {
  const result = {...req.headers};
  delete result['tailscale-user-login'];
  delete result['tailscale-user-name'];
  delete result['tailscale-user-profile-pic'];
  result['x-forwarded-host'] = new URL(config.origin).host;
  result['x-forwarded-proto'] = 'https';
  result.host = new URL(config.origin).host;
  return result;
}
const server = http.createServer((req, res) => {
  if (req.url === '/mail') { res.writeHead(302, {location: '/mail/'});res.end();return; }
  const [base, path] = target(req.url);
  const url = new URL(base);
  const upstream = http.request({hostname:url.hostname, port:url.port, method:req.method,
    path, headers:headers(req)}, response => {
      res.writeHead(response.statusCode, response.headers);response.pipe(res);
    });
  upstream.on('error', () => { if (!res.headersSent) res.writeHead(502);res.end('Preview service is starting or stopped.'); });
  req.on('aborted', () => upstream.destroy());
  res.on('close', () => upstream.destroy());
  req.pipe(upstream);
});
server.on('upgrade', (req, socket, head) => {
  const [base, path] = target(req.url);
  const url = new URL(base);
  const upstream = net.connect(Number(url.port), url.hostname, () => {
    upstream.write(`${req.method} ${path} HTTP/1.1\r\n` +
      Object.entries(headers(req)).map(([key,value]) => `${key}: ${value}`).join('\r\n')+'\r\n\r\n');
    if (head.length) upstream.write(head);
    socket.pipe(upstream).pipe(socket);
  });
  upstream.on('error', () => socket.destroy());socket.on('error', () => upstream.destroy());
  socket.on('close', () => upstream.destroy());
});
server.listen(config.port, '0.0.0.0', () => console.log(`Private preview: ${config.origin}`));
for (const signal of ['SIGINT','SIGTERM']) process.on(signal, () => server.close(() => process.exit(0)));
