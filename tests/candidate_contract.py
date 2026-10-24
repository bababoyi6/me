"""Acceptance contract for one tiny, private high-RTT candidate path check."""
import math

VERSION='telemt 3.5.5+tdh.balanced2'


def validate_wireguard(rows, counters):
    """Require encrypted traffic on both owned tunnels, without storing peer keys."""
    if set(counters)!={'wg-lab','wg-lab2'}:
        raise ValueError('both private WireGuard tunnels must be observed')
    for index,device in enumerate(('wg-lab','wg-lab2'),1):
        value=counters[device]
        expected=sum(r['bytes'] for r in rows if r['backend_index']==index)
        if (type(value.get('rx')) is not int or type(value.get('tx')) is not int or
                value['rx']<=0 or value['tx']<=0 or value['rx']+value['tx']<expected):
            raise ValueError('private encrypted tunnel traffic evidence incomplete')


def validate(rows, backends):
    if len(rows)!=7 or {(r['domain_index'],r['direction']) for r in rows[:4]}!={(1,'D'),(1,'U'),(2,'D'),(2,'U')}:
        raise ValueError('candidate path needs both domains and directions plus failover/recovery')
    if ([r['backend_index'] for r in rows[:4]]!=[1,1,2,2] or
            [r['backend_index'] for r in rows[4:]]!=[2,2,1] or
            [(r['domain_index'],r['direction']) for r in rows[4:]]!=[(1,'D'),(1,'U'),(1,'U')] or
            not all(r['sha256_verified'] is True for r in rows) or
            any(type(r['bytes']) is not int or r['bytes']<=0 for r in rows) or
            sum(r['bytes'] for r in rows)>2*1024**2):
        raise ValueError('candidate payload integrity, routing or size contract failed')
    if len(backends)!=2:
        raise ValueError('two independent candidate backends required')
    observed=[]
    for state in backends:
        if state['telemt']!=VERSION or state['errors'] or state.get('socket_observation_errors'):
            raise ValueError('candidate identity or backend telemetry failed')
        values=[s['rtt_ms'] for s in state.get('client_sockets',[])
                if type(s.get('rtt_ms')) in (int,float) and math.isfinite(s['rtt_ms']) and s['rtt_ms']>0]
        if not values or not any(value>5 for value in values):
            raise ValueError('high RTT entry-to-backend TCP hop was not observed')
        observed.append({'sampled_min_rtt_ms':min(values),'sampled_max_rtt_ms':max(values),'samples':len(values)})
    return observed
