"""Fixed long-connection schedule, payload ceiling and final acceptance."""
RATE = 1024
PAYLOAD_MAX = 192*1024**2


def schedule(seconds):
    if seconds not in (60,86400):
        raise ValueError('only the pilot or one day is supported')
    hours = (seconds+3599)//3600
    minutes = (seconds+59)//60
    return {'seconds':seconds,'rate_bytes_sec':RATE,'long_flows':2,'short_files':hours*4,
            'probes':minutes,'payload_bytes':2*seconds*RATE+hours*4*131072+minutes*1024}


def validate(result):
    expected = schedule(result['seconds_requested'])
    rows = result['rows']
    long = [r for r in rows if r['kind']=='long']
    if (result.get('complete') is not True or result.get('backend_errors') != [[],[]] or
            result.get('backend_identity_unchanged') is not True or result.get('initial_route_delta') != [1,1] or
            len(rows)!=2+expected['short_files']+expected['probes'] or len(long)!=2 or
            sum(r['bytes'] for r in rows)!=expected['payload_bytes'] or expected['payload_bytes']>PAYLOAD_MAX or
            any(r.get('sha256_verified') is not True for r in rows) or
            any(r.get('persistent_dc') is not True or r['elapsed_seconds']<expected['seconds'] for r in long)):
        raise ValueError('incomplete long-connection integrity or schedule')
    if { (r['domain_index'],r['direction']) for r in long }!={(1,'D'),(2,'U')}:
        raise ValueError('both assigned persistent directions required')
    return True
