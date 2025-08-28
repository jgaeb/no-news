## Issues

Which if any of the following important national issues from {{year}} best
describe the news segment?

{% for issue in issues %}
* **{{issue.title}}**<br><small>{{issue.description}}</small><br>
{% endfor %}
