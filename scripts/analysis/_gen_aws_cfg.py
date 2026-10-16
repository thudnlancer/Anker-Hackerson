import json, base64, os
cfg = json.load(open('config/local/local_online_config.json', encoding='utf-8'))
cfg['data'] = {
  'training_zip_local_path': '/home/ec2-user/solarbank-experiment/data/solarbank-training-data.zip',
  'model_directory': '/home/ec2-user/solarbank-experiment/models',
  'output_directory': '/home/ec2-user/solarbank-experiment/outputs',
  'log_directory': '/home/ec2-user/solarbank-experiment/logs'}
out = os.path.join(os.environ['TEMP'], 'cfg.b64')
open(out, 'w').write(base64.b64encode(json.dumps(cfg, indent=2).encode()).decode())
print(out)
