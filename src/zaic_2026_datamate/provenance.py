"""Validate served checkpoint identity against declared training provenance."""

def verify_model_provenance(metadata, data_sha256, split_sha256, businesses, model):
    expected = {'model':model, 'training_complete':True, 'merge_complete':True,
                'data_sha256':data_sha256, 'split_sha256':split_sha256,
                'base_model':'Qwen/Qwen2.5-Coder-7B-Instruct',
                'base_revision':'c03e6d358207e414f1eca0bb1891e29f1db0e242'}
    if not isinstance(metadata,dict) or any(metadata.get(k)!=v for k,v in expected.items()):
        raise ValueError('Served model provenance does not match the requested trained checkpoint')
    if sorted(metadata.get('businesses',[])) != sorted(businesses):
        raise ValueError('Served model training businesses differ from the training partition')
