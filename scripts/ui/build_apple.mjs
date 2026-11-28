import fs from 'node:fs';
const T = fs.readFileSync('scripts/ui/apple-tokens.css','utf8');
const C = fs.readFileSync('scripts/ui/apple-components.css','utf8');
function build(name, title, cssF, htmlF, jsF){
  const css = fs.readFileSync('scripts/ui/'+cssF,'utf8');
  const body = fs.readFileSync('scripts/ui/'+htmlF,'utf8');
  const js = fs.readFileSync('scripts/ui/'+jsF,'utf8');
  const head = ['<!DOCTYPE html>','<html lang=zh-CN data-theme=dark>','<head>','<meta charset=UTF-8>',
    '<meta name=viewport content=width=device-width,initial-scale=1.0>','<title>'+title+'</title>',
    '<style>'+T+C+css+'</style>','</head>','<body>'].join('\n');
  const tail = ['<script>'+js+'</script>','</body>','</html>',''].join('\n');
  fs.mkdirSync('docs/preview',{recursive:true});
  fs.writeFileSync('docs/preview/'+name+'.html', head+body+tail, 'utf8');
  console.log(name+' '+((head+body+tail).length/1024).toFixed(1)+' KB');
}
build('variant-e-apple-stocks','Quant A-Share - Apple Stocks 式','vE.css','vE.html','vE.js');
build('variant-f-apple-numbers','Quant A-Share - Apple Numbers 式','vF.css','vF.html','vF.js');
